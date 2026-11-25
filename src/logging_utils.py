"""Read, filter, and safely emit Beakon's rotating application log."""

import base64
import hashlib
import json
import logging
import os
import re
from contextlib import ExitStack


LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
LOG_FORMAT = ('%(asctime)s [%(process)-5d:%(thread)#x] %(name)s %(levelname)-5s '
              '[in %(module)s @ %(pathname)s:%(lineno)d] %(message)s')

# New records put formatter-controlled source metadata before the message. The
# legacy expression remains for existing log files, whose source followed it.
LOG_HEADER_RE = re.compile(
    r'^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) '
    r'\[(?P<process>\d+):(?P<thread>[^\]]+)\] '
    r'(?P<logger>\S+) (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)[ \t]+'
    r'\[in (?P<module>[^@\r\n]+?) @ (?P<pathname>[^\r\n]*?):(?P<lineno>\d+)\]'
    r'[ \t]?(?P<message>.*)$'
)
LOG_LEGACY_HEADER_RE = re.compile(
    r'^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) '
    r'\[(?P<process>\d+):(?P<thread>[^\]]+)\] '
    r'(?P<logger>\S+) (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)'
    r'(?:[ \t](?P<message>.*))?$'
)
LOG_SOURCE_RE = re.compile(
    r' \[in (?P<module>[^@\r\n]+?) @ '
    r'(?P<pathname>[^\r\n]*?):(?P<lineno>\d+)\]'
)
_TRACEBACK_RE = re.compile(r'^[ \t]*Traceback \(most recent call last\):$', re.MULTILINE)
_SNAPSHOT_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{1,8192}$')


def _indent_exception_text(text):
    """Indent a traceback once, including text cached by an earlier handler."""
    lines = text.splitlines()
    if lines and all(not line or line.startswith('    ') for line in lines):
        return text
    return '\n'.join('    ' + line if line else '' for line in lines)


class BeakonLogFormatter(logging.Formatter):
    """Unambiguous record format with indented, non-forgeable traceback lines."""

    def __init__(self):
        super().__init__(LOG_FORMAT)

    def formatException(self, exc_info):
        return _indent_exception_text(super().formatException(exc_info))

    def format(self, record):
        # Flask installs a plain stream handler before the rotating file handler.
        # That handler may cache record.exc_text first, bypassing formatException.
        # Normalize cached text here so handler order cannot restore raw newlines.
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        elif record.exc_text:
            record.exc_text = _indent_exception_text(record.exc_text)
        return super().format(record)


def _single_line(value):
    """Escape physical line breaks in user-controlled log fields."""
    if isinstance(value, str):
        return value.replace('\r', '\\r').replace('\n', '\\n')
    return value


def sanitise_log_record(record):
    """Prevent message/argument newlines from forging new log records."""
    record.msg = _single_line(record.msg)
    if isinstance(record.args, dict):
        record.args = {key: _single_line(value) for key, value in record.args.items()}
    elif isinstance(record.args, tuple):
        record.args = tuple(_single_line(value) for value in record.args)
    return record


class SingleLineLogFilter(logging.Filter):
    """Apply log-injection protection to every normally emitted app record."""

    def filter(self, record):
        sanitise_log_record(record)
        return True


class LogSnapshotExpired(ValueError):
    """A pagination snapshot's files were rotated out or changed."""


def _unknown_entry(raw_lines, start_offset):
    raw = '\n'.join(raw_lines)
    return {
        'timestamp': None,
        'level': 'UNKNOWN',
        'logger': None,
        'message': raw,
        'source': None,
        'raw': raw,
        'start_offset': start_offset,
    }


def _parse_log_lines(lines):
    """Parse one file, retaining multi-line messages and exception tracebacks."""
    entries = []
    unknown_lines = []
    unknown_offset = None
    current = None

    def flush_unknown():
        nonlocal unknown_offset
        if unknown_lines:
            entries.append(_unknown_entry(unknown_lines, unknown_offset))
            unknown_lines.clear()
            unknown_offset = None

    def finish_current():
        """Finish the current record, or return False if it is incomplete."""
        message = '\n'.join(current['message_lines'])
        fields = current['header']
        if current['structured_source']:
            source = '%s @ %s:%s' % (
                fields['module'], fields['pathname'], fields['lineno'])
        else:
            # Exception text follows the legacy source suffix. Never search it
            # for source-like strings or allow its header-shaped lines to split
            # the record.
            traceback = _TRACEBACK_RE.search(message)
            metadata = message[:traceback.start()] if traceback else message
            exception_text = message[traceback.start():] if traceback else ''
            source_matches = list(LOG_SOURCE_RE.finditer(metadata))
            if not source_matches:
                return False
            source_match = source_matches[-1]
            source = '%s @ %s:%s' % (
                source_match.group('module'), source_match.group('pathname'),
                source_match.group('lineno'))
            message = metadata[:source_match.start()] + metadata[source_match.end():]
            if not message.strip() and not exception_text:
                message = ''
            message += exception_text

        entries.append({
            'timestamp': fields['timestamp'],
            'level': fields['level'],
            'logger': fields['logger'],
            'message': message,
            'source': source,
            'raw': '\n'.join(current['raw_lines']),
            'start_offset': current['start_offset'],
        })
        current.clear()
        return True

    def preserve_incomplete_current():
        nonlocal unknown_offset
        unknown_lines.extend(current['raw_lines'])
        if unknown_offset is None:
            unknown_offset = current['start_offset']
        current.clear()

    def legacy_traceback_has_continuation(start_index):
        """Best-effort boundary detection for ambiguous historical tracebacks."""
        for _offset, next_line in lines[start_index:]:
            text = next_line.decode('utf-8', errors='replace').rstrip('\r\n')
            if not text.strip():
                continue
            return not (LOG_HEADER_RE.match(text) or LOG_LEGACY_HEADER_RE.match(text))
        return False

    for index, (offset, physical_line) in enumerate(lines):
        line = physical_line.decode('utf-8', errors='replace').rstrip('\r\n')
        header = LOG_HEADER_RE.match(line)
        legacy_header = LOG_LEGACY_HEADER_RE.match(line) if not header else None

        if header or legacy_header:
            if (current is not None and
                    not current['structured_source'] and
                    _TRACEBACK_RE.search('\n'.join(current['message_lines'])) and
                    legacy_traceback_has_continuation(index + 1)):
                # A header-shaped line followed by more exception text belongs
                # to that legacy traceback. A following header/EOF is the best
                # reliable boundary available in the old unstructured format.
                current['raw_lines'].append(line)
                current['message_lines'].append(line)
                continue
            if current is not None:
                if finish_current():
                    flush_unknown()
                elif header:
                    # A structured header is unambiguous. A malformed preceding
                    # record must not consume this valid one (notably when the
                    # preceding message was empty).
                    preserve_incomplete_current()
                    flush_unknown()
                else:
                    # Legacy header-looking text may be part of a multi-line
                    # message; retain it unless the prior record was complete.
                    current['raw_lines'].append(line)
                    current['message_lines'].append(line)
                    continue
            flush_unknown()
            fields = (header or legacy_header).groupdict()
            initial_message = fields.get('message') or ''
            if legacy_header:
                # The legacy formatter padded short level names to five columns
                # before its literal separator. Remove only that padding.
                padding = max(0, 5 - len(fields['level']))
                if initial_message[:padding] == ' ' * padding:
                    initial_message = initial_message[padding:]
            current = {
                'header': fields,
                'structured_source': header is not None,
                'raw_lines': [line],
                'message_lines': [initial_message],
                'start_offset': offset,
            }
        elif current is None:
            unknown_lines.append(line)
            if unknown_offset is None:
                unknown_offset = offset
        else:
            current['raw_lines'].append(line)
            current['message_lines'].append(line)

    if current is not None and not finish_current():
        preserve_incomplete_current()
    flush_unknown()
    return entries


def _encode_snapshot(files):
    raw = json.dumps(files, separators=(',', ':'), sort_keys=True).encode('ascii')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _decode_snapshot(token):
    if not isinstance(token, str) or not _SNAPSHOT_TOKEN_RE.fullmatch(token):
        raise ValueError('Invalid log snapshot.')
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_',
                               validate=True)
        files = json.loads(raw.decode('ascii'))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Invalid log snapshot.')
    if not isinstance(files, list) or len(files) > 100:
        raise ValueError('Invalid log snapshot.')

    orders = set()
    identities = set()
    for spec in files:
        if not isinstance(spec, dict):
            raise ValueError('Invalid log snapshot.')
        if set(spec) != {'order', 'device', 'inode', 'size', 'sha256'}:
            raise ValueError('Invalid log snapshot.')
        if any(not isinstance(spec[key], int) or isinstance(spec[key], bool)
               for key in ('order', 'device', 'inode', 'size')):
            raise ValueError('Invalid log snapshot.')
        identity = (spec['device'], spec['inode'])
        if (not 0 <= spec['order'] <= 10000 or spec['order'] in orders or
                identity in identities):
            raise ValueError('Invalid log snapshot.')
        identities.add(identity)
        if (spec['device'] < 0 or spec['inode'] < 0 or
                not 0 <= spec['size'] <= 64 * 1024 * 1024):
            raise ValueError('Invalid log snapshot.')
        if not isinstance(spec['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', spec['sha256']):
            raise ValueError('Invalid log snapshot.')
        orders.add(spec['order'])
    return sorted(files, key=lambda spec: spec['order'])


def _lines_with_offsets(data):
    lines = []
    offset = 0
    for line in data.splitlines(keepends=True):
        lines.append((offset, line))
        offset += len(line)
    return lines


def read_log_snapshot(log_path, backup_count=10, snapshot=None):
    """Return ``(entries, snapshot_token)`` for one immutable log-file set.

    The token pins each file's device/inode, byte length, and content hash. Later
    pages read exactly those bytes even if new records arrive or files rotate to
    another backup name. A file rotated out of the retention window expires the
    snapshot rather than silently duplicating or omitting records.
    """
    paths = ['%s.%d' % (log_path, number)
             for number in range(backup_count, 0, -1)]
    paths.append(log_path)
    entries = []

    with ExitStack() as stack:
        opened = []
        for order, path in enumerate(paths):
            try:
                log_file = stack.enter_context(open(path, 'rb'))
            except FileNotFoundError:
                continue
            stat = os.fstat(log_file.fileno())
            opened.append((order, log_file, stat.st_dev, stat.st_ino))

        if snapshot is None:
            selected = []
            for order, log_file, device, inode in opened:
                size = os.fstat(log_file.fileno()).st_size
                log_file.seek(0)
                data = log_file.read(size)
                selected.append(({
                    'order': order, 'device': device, 'inode': inode,
                    'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(),
                }, data))
        else:
            specs = _decode_snapshot(snapshot)
            by_identity = {(device, inode): (log_file, order)
                           for order, log_file, device, inode in opened}
            selected = []
            for spec in specs:
                match = by_identity.get((spec['device'], spec['inode']))
                if match is None:
                    raise LogSnapshotExpired(
                        'The log snapshot expired because files rotated. Refresh filters to start again.')
                log_file, _order = match
                log_file.seek(0)
                data = log_file.read(spec['size'])
                if len(data) != spec['size'] or hashlib.sha256(data).hexdigest() != spec['sha256']:
                    raise LogSnapshotExpired(
                        'The log snapshot expired because files rotated. Refresh filters to start again.')
                selected.append((spec, data))

        for spec, data in selected:
            for entry in _parse_log_lines(_lines_with_offsets(data)):
                entry['key'] = '%s|%020d|%020d|%020d|%020d' % (
                    entry['timestamp'] or '0000-00-00 00:00:00,000',
                    spec['order'], entry['start_offset'],
                    spec['device'], spec['inode'])
                entries.append(entry)

    entries.sort(key=lambda entry: entry['key'], reverse=True)
    return entries, _encode_snapshot([spec for spec, _data in selected])


def read_log_entries(log_path, backup_count=10):
    """Compatibility wrapper returning only entries from a fresh snapshot."""
    return read_log_snapshot(log_path, backup_count=backup_count)[0]


def filter_log_entries(entries, level='ALL', search='', start=None, end=None):
    """Filter parsed entries by severity, case-insensitive text, and time range.

    ``start`` and ``end`` are normalized ``YYYY-MM-DD HH:MM:SS,mmm`` strings and
    are inclusive. Entries without a parseable timestamp are excluded whenever a
    date boundary is supplied.
    """
    level = str(level or 'ALL').upper()
    search = str(search or '').casefold()
    matches = []

    for entry in entries:
        if level != 'ALL' and entry['level'] != level:
            continue
        if search and search not in entry['raw'].casefold():
            continue
        timestamp = entry['timestamp']
        if start or end:
            if timestamp is None:
                continue
            if start and timestamp < start:
                continue
            if end and timestamp > end:
                continue
        matches.append(entry)

    return matches
