/**
 * ProgressModal — SSE-driven progress feedback for long operations.
 *
 * Usage:
 *   ProgressModal.start('/qa/ask-stream', {
 *     method: 'POST',
 *     body: formData,
 *     title: 'Answering Question',
 *     onComplete: (data) => { /* handle result */ }
 *   });
 */
const ProgressModal = (() => {
    let _onComplete = null;
    let _onError = null;
    let _abortController = null;

    function _el(id) { return document.getElementById(id); }

    function open(title) {
        _el('progress-title').textContent = title || 'Processing...';
        _el('progress-bar').style.width = '0%';
        _el('progress-bar').style.background = 'linear-gradient(90deg, #b45309, #d99a3a)';
        _el('progress-step').textContent = 'Initializing...';
        _el('progress-error').classList.add('hidden');
        _el('progress-actions').classList.add('hidden');
        _el('progress-modal').classList.remove('hidden');
    }

    function update(pct, step) {
        if (pct !== undefined && pct !== null) {
            _el('progress-bar').style.width = Math.min(100, Math.max(0, pct)) + '%';
        }
        if (step) {
            _el('progress-step').textContent = step;
        }
    }

    function showError(message) {
        _el('progress-error-text').textContent = message;
        _el('progress-error').classList.remove('hidden');
        _el('progress-actions').classList.remove('hidden');
        _el('progress-bar').style.width = '100%';
        _el('progress-bar').style.background = '#991b1b';
    }

    function close() {
        _el('progress-modal').classList.add('hidden');
        if (_abortController) {
            _abortController.abort();
            _abortController = null;
        }
    }

    /**
     * Start a progress-tracked operation via SSE.
     *
     * Events expected from server:
     *   event: progress  data: {"pct": 30, "step": "Searching documents..."}
     *   event: complete  data: {"html": "<div>...</div>"}
     *   event: error     data: {"message": "Something went wrong"}
     */
    function start(url, options) {
        options = options || {};
        var method = options.method || 'POST';
        var body = options.body || null;
        var title = options.title || 'Processing';
        _onComplete = options.onComplete || null;
        _onError = options.onError || null;

        open(title);

        _abortController = new AbortController();

        var fetchOptions = {
            method: method,
            body: body,
            signal: _abortController.signal,
        };

        // Don't set Content-Type for FormData (browser sets it with boundary)
        if (!(body instanceof FormData)) {
            fetchOptions.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
        }

        fetch(url, fetchOptions)
        .then(function(response) {
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }

            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';

            function processStream() {
                reader.read().then(function(result) {
                    if (result.done) {
                        close();
                        return;
                    }

                    buffer += decoder.decode(result.value, { stream: true });

                    // Parse SSE events from buffer
                    var lines = buffer.split('\n');
                    buffer = lines.pop(); // Keep incomplete line

                    var eventType = null;
                    var eventData = '';

                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i];
                        if (line.indexOf('event: ') === 0) {
                            eventType = line.slice(7).trim();
                        } else if (line.indexOf('data: ') === 0) {
                            eventData = line.slice(6);
                        } else if (line === '' && eventType) {
                            _handleEvent(eventType, eventData);
                            eventType = null;
                            eventData = '';
                        }
                    }

                    processStream();
                }).catch(function(err) {
                    if (err.name !== 'AbortError') {
                        showError('Connection lost: ' + err.message);
                    }
                });
            }

            processStream();
        })
        .catch(function(err) {
            if (err.name !== 'AbortError') {
                showError('Request failed: ' + err.message);
            }
        });
    }

    function _handleEvent(type, rawData) {
        var data;
        try {
            data = JSON.parse(rawData);
        } catch(e) {
            data = { message: rawData };
        }

        switch (type) {
            case 'progress':
                update(data.pct, data.step);
                break;

            case 'complete':
                update(100, 'Done!');
                setTimeout(function() {
                    close();
                    if (_onComplete) _onComplete(data);
                }, 400);
                break;

            case 'error':
                showError(data.message || 'An error occurred');
                if (_onError) _onError(data);
                break;
        }
    }

    return { open: open, update: update, showError: showError, close: close, start: start };
})();
