// Filterable application log viewer. Log content is always inserted with
// textContent; a log line (or traceback) must never be interpreted as HTML.
(function () {
    'use strict';

    var form = document.getElementById('log-filters');
    var container = document.getElementById('log-entries');
    if (!form || !container) return;

    var status = document.getElementById('log-status');
    var error = document.getElementById('log-error');
    var empty = document.getElementById('log-empty');
    var loading = document.getElementById('log-loading');
    var loadMore = document.getElementById('load-more-logs');
    var resetButton = document.getElementById('reset-log-filters');
    var levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'UNKNOWN'];
    var state = {
        count: parseInt(container.dataset.count, 10) || 0,
        total: parseInt(container.dataset.total, 10) || 0,
        snapshot: container.dataset.snapshot || '',
        nextCursor: container.dataset.nextCursor || '',
        hasMore: container.dataset.hasMore === 'true',
        loading: false
    };

    function showError(message) {
        error.textContent = message;
        error.hidden = false;
    }

    function updateSummary() {
        if (state.total === 0) {
            empty.hidden = false;
            status.hidden = true;
            return;
        }
        empty.hidden = true;
        status.hidden = false;
        status.textContent = 'Showing ' + state.count + ' of ' + state.total +
            ' matching ' + (state.total === 1 ? 'entry.' : 'entries.');
    }

    function createLogEntry(entry) {
        var level = levels.indexOf(String(entry.level || '').toUpperCase());
        if (level === -1) level = levels.length - 1;

        var article = document.createElement('article');
        article.className = 'log-entry';
        article.dataset.level = levels[level].toLowerCase();

        var header = document.createElement('div');
        header.className = 'log-entry-header';

        var badge = document.createElement('span');
        badge.className = 'label log-level log-level-' + levels[level].toLowerCase();
        badge.textContent = levels[level];
        header.appendChild(badge);

        var timestamp = document.createElement('time');
        timestamp.className = 'log-timestamp';
        timestamp.textContent = entry.timestamp || 'Unknown time';
        header.appendChild(timestamp);

        if (entry.logger) {
            var logger = document.createElement('span');
            logger.className = 'log-logger';
            logger.textContent = entry.logger;
            header.appendChild(logger);
        }
        article.appendChild(header);

        var message = document.createElement('pre');
        message.className = 'log-message';
        message.textContent = entry.message || '';
        article.appendChild(message);

        if (entry.source) {
            var details = document.createElement('details');
            details.className = 'log-source';
            var summary = document.createElement('summary');
            summary.textContent = 'Source';
            var source = document.createElement('code');
            source.textContent = entry.source;
            details.appendChild(summary);
            details.appendChild(source);
            article.appendChild(details);
        }
        return article;
    }

    function filterParams() {
        var params = new URLSearchParams();
        var search = document.getElementById('log-search').value.trim();
        var level = document.getElementById('log-level-filter').value.toUpperCase();
        var start = document.getElementById('log-start').value;
        var end = document.getElementById('log-end').value;
        if (search) params.set('q', search);
        if (level && level !== 'ALL') params.set('level', level);
        if (start) params.set('start', start);
        if (end) params.set('end', end);
        params.set('limit', '100');
        return params;
    }

    function setControlsDisabled(disabled) {
        Array.prototype.forEach.call(form.querySelectorAll('input, select, button'), function (control) {
            control.disabled = disabled;
        });
        if (loadMore) loadMore.disabled = disabled;
    }

    function loadLogs(reset) {
        if (state.loading) return;
        state.loading = true;
        setControlsDisabled(true);
        loading.hidden = false;
        error.hidden = true;

        var params = filterParams();
        if (!reset) {
            if (state.snapshot) params.set('snapshot', state.snapshot);
            if (state.nextCursor) params.set('cursor', state.nextCursor);
        }

        fetch('/admin/api/logs?' + params.toString(), {
            method: 'GET',
            headers: {'Accept': 'application/json'}
        }).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                if (!res.ok) {
                    throw new Error(data.error || 'Unable to load application logs.');
                }
                if (!Array.isArray(data.entries) ||
                        (data.next_cursor !== null && typeof data.next_cursor !== 'string')) {
                    throw new Error('Unable to load application logs.');
                }
                return data;
            });
        }).then(function (data) {
            if (reset) container.textContent = '';
            data.entries.forEach(function (entry) {
                container.appendChild(createLogEntry(entry));
            });

            state.count = (parseInt(data.offset, 10) || 0) + data.entries.length;
            state.total = parseInt(data.total, 10) || 0;
            state.snapshot = data.snapshot || '';
            state.nextCursor = data.next_cursor || '';
            state.hasMore = !!state.nextCursor;
            container.dataset.count = String(state.count);
            container.dataset.total = String(state.total);
            container.dataset.snapshot = state.snapshot;
            container.dataset.nextCursor = state.nextCursor;
            container.dataset.hasMore = String(state.hasMore);
            if (loadMore) {
                loadMore.dataset.nextCursor = state.nextCursor;
                loadMore.hidden = !state.hasMore;
            }

            if (state.snapshot) params.set('snapshot', state.snapshot);
            var query = params.toString();
            window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
            updateSummary();
        }).catch(function (err) {
            showError(err.message || 'Unable to load application logs. Please try again.');
        }).finally(function () {
            state.loading = false;
            setControlsDisabled(false);
            loading.hidden = true;
        });
    }

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        loadLogs(true);
    });

    if (resetButton) {
        resetButton.addEventListener('click', function () {
            document.getElementById('log-search').value = '';
            document.getElementById('log-level-filter').value = 'ALL';
            document.getElementById('log-start').value = '';
            document.getElementById('log-end').value = '';
            loadLogs(true);
        });
    }

    if (loadMore) {
        loadMore.addEventListener('click', function () { loadLogs(false); });
    }
}());
