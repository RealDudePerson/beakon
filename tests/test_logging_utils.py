"""Unit tests for parsing and filtering Beakon's rotating log files."""

import io
import logging

from logging_utils import (BeakonLogFormatter, SingleLineLogFilter,
                           filter_log_entries, read_log_entries,
                           sanitise_log_record)


def log_line(timestamp, level, message, logger='app'):
    return ('%s [12345:0xabc] %s %-5s %s '
            '[in worker @ /srv/beakon/src/worker.py:42]' %
            (timestamp, logger, level, message))


def test_reads_backups_and_active_file_newest_first(tmp_path):
    path = tmp_path / 'beakon.log'
    (tmp_path / 'beakon.log.2').write_text(
        log_line('2026-11-24 08:00:00,000', 'DEBUG', 'oldest') + '\n')
    (tmp_path / 'beakon.log.1').write_text(
        log_line('2026-11-24 09:00:00,000', 'INFO', 'middle') + '\n')
    path.write_text(log_line('2026-11-24 10:00:00,000', 'ERROR', 'newest') + '\n')

    entries = read_log_entries(str(path))

    assert [entry['message'] for entry in entries] == ['newest', 'middle', 'oldest']
    assert entries[0]['level'] == 'ERROR'
    assert entries[0]['logger'] == 'app'
    assert entries[0]['source'] == 'worker @ /srv/beakon/src/worker.py:42'


def test_message_can_contain_source_like_text(tmp_path):
    path = tmp_path / 'beakon.log'
    message = 'forwarded [in fake @ elsewhere.py:1]'
    path.write_text(log_line('2026-11-24 10:00:00,000', 'INFO', message) + '\n')

    entry = read_log_entries(str(path))[0]

    assert entry['message'] == message
    assert entry['source'] == 'worker @ /srv/beakon/src/worker.py:42'


def test_multiline_message_is_one_record(tmp_path):
    path = tmp_path / 'beakon.log'
    path.write_text(log_line('2026-11-24 10:00:00,000', 'INFO',
                             'first\nsecond line') + '\n'
                    + log_line('2026-11-24 10:00:01,000', 'DEBUG', 'next') + '\n')

    entries = read_log_entries(str(path))

    assert len(entries) == 2
    assert entries[1]['level'] == 'INFO'
    assert entries[1]['message'] == 'first\nsecond line'
    assert entries[1]['timestamp'] == '2026-11-24 10:00:00,000'


def test_header_like_text_inside_message_does_not_forge_a_record(tmp_path):
    path = tmp_path / 'beakon.log'
    forged = ('2026-11-24 10:00:00,500 [12345:0xabc] app CRITICAL '
              'forged [in fake @ elsewhere.py:1]')
    path.write_text(log_line('2026-11-24 10:00:00,000', 'INFO',
                             'first\n' + forged + '\nlast line') + '\n')

    entries = read_log_entries(str(path))

    assert len(entries) == 1
    assert entries[0]['level'] == 'INFO'
    assert entries[0]['timestamp'] == '2026-11-24 10:00:00,000'
    assert forged in entries[0]['message']


def test_duplicate_records_have_distinct_pagination_keys(tmp_path):
    path = tmp_path / 'beakon.log'
    line = log_line('2026-11-24 10:00:00,000', 'INFO', 'same') + '\n'
    path.write_text(line + line)

    entries = read_log_entries(str(path))

    assert len(entries) == 2
    assert entries[0]['key'] != entries[1]['key']
    assert entries[0]['key'] > entries[1]['key']


def test_actual_logger_exception_cannot_forge_metadata_or_records(tmp_path):
    default_stream = io.StringIO()
    file_stream = io.StringIO()
    # Match Flask's order: its plain stream handler formats and caches exc_text
    # before the Beakon file formatter gets to normalize the same record.
    logger = logging.Logger('app', level=logging.DEBUG)
    logger.addHandler(logging.StreamHandler(default_stream))
    file_handler = logging.StreamHandler(file_stream)
    file_handler.setFormatter(BeakonLogFormatter())
    logger.addHandler(file_handler)
    logger.propagate = False
    forged = ('2026-11-24 10:00:00,500 [12345:0xabc] app CRITICAL forged '
              '[in fake @ elsewhere.py:1]')
    try:
        raise ValueError('failure\n' + forged + '\nends [in fake @ elsewhere.py:2]')
    except ValueError:
        logger.exception('request failed')
    logger.info('next real record')

    path = tmp_path / 'beakon.log'
    path.write_text(file_stream.getvalue())
    entries = read_log_entries(str(path))

    assert forged in default_stream.getvalue()
    assert len(entries) == 2
    assert entries[0]['message'] == 'next real record'
    assert entries[1]['level'] == 'ERROR'
    assert 'request failed' in entries[1]['message']
    assert forged in entries[1]['message']
    assert 'elsewhere.py' not in entries[1]['source']
    assert entries[1]['source'].startswith('test_logging_utils @')


def test_legacy_traceback_header_and_source_like_text_stays_with_parent(tmp_path):
    path = tmp_path / 'beakon.log'
    forged = ('2026-11-24 10:00:00,500 [12345:0xabc] app CRITICAL '
              '[in fake @ elsewhere.py:1] forged')
    path.write_text(
        log_line('2026-11-24 10:00:00,000', 'ERROR', 'request failed') + '\n'
        'Traceback (most recent call last):\n'
        '  File "worker.py", line 42, in run\n'
        'ValueError: failure\n'
        + forged + '\n'
        'ends [in fake @ elsewhere.py:2]\n'
        + log_line('2026-11-24 10:00:01,000', 'INFO', 'next real record') + '\n')

    entries = read_log_entries(str(path))

    assert len(entries) == 2
    assert entries[0]['message'] == 'next real record'
    assert entries[1]['level'] == 'ERROR'
    assert forged in entries[1]['message']
    assert entries[1]['source'] == 'worker @ /srv/beakon/src/worker.py:42'


def test_empty_messages_do_not_consume_the_next_record(tmp_path):
    path = tmp_path / 'beakon.log'
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(BeakonLogFormatter())
    logger = logging.Logger('app', level=logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    logger.info('')
    logger.info('next')
    path.write_text(stream.getvalue())

    entries = read_log_entries(str(path))
    assert [entry['message'] for entry in entries] == ['next', '']

    # The same edge case exists in historical source-after-message records.
    path.write_text(log_line('2026-11-24 10:00:00,000', 'INFO', '') + '\n'
                    + log_line('2026-11-24 10:00:01,000', 'INFO', 'next') + '\n')
    entries = read_log_entries(str(path))
    assert [entry['message'] for entry in entries] == ['next', '']


def test_log_filter_escapes_newlines_in_messages_and_arguments():
    record = logging.LogRecord('app', logging.INFO, __file__, 1,
                               'first\n%s', ('second\r\nthird',), None)
    sanitised = sanitise_log_record(record)

    assert sanitised.msg == 'first\\n%s'
    assert sanitised.args == ('second\\r\\nthird',)
    assert SingleLineLogFilter().filter(record) is True


def test_traceback_stays_with_parent_and_unparsed_lines_are_retained(tmp_path):
    path = tmp_path / 'beakon.log'
    path.write_text(
        'startup text without a log prefix\n' +
        log_line('2026-11-24 10:00:00,000', 'ERROR', 'request failed') + '\n' +
        'Traceback (most recent call last):\n' +
        '  File "worker.py", line 42, in run\n' +
        'ValueError: bad value\n')

    entries = read_log_entries(str(path))

    assert len(entries) == 2
    assert entries[0]['message'].startswith('request failed\nTraceback')
    assert 'ValueError: bad value' in entries[0]['raw']
    assert entries[1]['level'] == 'UNKNOWN'
    assert entries[1]['timestamp'] is None
    assert entries[1]['message'] == 'startup text without a log prefix'


def test_filters_by_level_case_insensitive_text_and_inclusive_dates(tmp_path):
    path = tmp_path / 'beakon.log'
    path.write_text(
        log_line('2026-11-24 09:59:59,999', 'INFO', 'before window') + '\n' +
        log_line('2026-11-24 10:00:00,000', 'WARNING', 'Disk nearly full') + '\n' +
        log_line('2026-11-24 10:00:59,999', 'ERROR', 'disk write failed') + '\n' +
        log_line('2026-11-24 10:01:00,000', 'CRITICAL', 'after window') + '\n')
    entries = read_log_entries(str(path))

    warnings = filter_log_entries(entries, level='warning')
    assert [entry['message'] for entry in warnings] == ['Disk nearly full']

    text_matches = filter_log_entries(entries, search='DISK')
    assert {entry['level'] for entry in text_matches} == {'WARNING', 'ERROR'}

    window = filter_log_entries(
        entries,
        start='2026-11-24 10:00:00,000',
        end='2026-11-24 10:00:59,999',
    )
    assert [entry['message'] for entry in window] == ['disk write failed', 'Disk nearly full']


def test_date_filter_excludes_unparsed_entries(tmp_path):
    path = tmp_path / 'beakon.log'
    path.write_text('unparsed\n' + log_line(
        '2026-11-24 10:00:00,000', 'INFO', 'parsed') + '\n')
    entries = read_log_entries(str(path))

    assert len(filter_log_entries(entries, start='2026-11-24 00:00:00,000')) == 1
    assert len(filter_log_entries(entries, level='UNKNOWN')) == 1


def test_missing_log_is_empty(tmp_path):
    assert read_log_entries(str(tmp_path / 'missing.log')) == []
