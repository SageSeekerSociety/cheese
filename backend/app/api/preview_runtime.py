"""Opt-in bridge served on the authorized content origin, never injected into HTML."""

RUNTIME_SCRIPT = r"""(() => {
  'use strict';
  let hello = null;
  let state = null;
  function publish() {
    if (!hello || !state) return;
    window.parent.postMessage({
      channel: 'cheese-preview-runtime', version: 1,
      sessionId: hello.sessionId, ...state,
    }, hello.origin);
  }
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window.parent || event.origin !== __PLATFORM_ORIGIN__ ||
        !data || data.channel !== 'cheese-preview-runtime' || data.version !== 1 ||
        data.type !== 'hello' || typeof data.sessionId !== 'string' ||
        data.sessionId.length > 128) return;
    hello = { sessionId: data.sessionId, origin: event.origin };
    publish();
  });
  window.CheesePreviewRuntime = Object.freeze({
    ready() { state = { type: 'ready' }; publish(); },
    error(message) {
      state = { type: 'error', message: String(message).slice(0, 1000) };
      publish();
    },
  });
})();
"""
