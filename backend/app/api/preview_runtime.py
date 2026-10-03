"""Opt-in bridge served on the authorized content origin, never injected into HTML."""

RUNTIME_SCRIPT = r"""(() => {
  'use strict';
  let hello = null;
  let state = null;
  let keys = [];
  function publish() {
    if (!hello || !state) return;
    window.parent.postMessage({
      channel: 'cheese-preview-runtime', version: 1,
      sessionId: hello.sessionId, ...state,
    }, hello.origin);
  }
  function usableKey(key) {
    if (key === null || typeof key !== 'object') return false;
    return typeof key.id === 'string' && /^[a-z0-9_.-]{1,64}$/i.test(key.id) &&
      typeof key.code === 'string' && key.code.length <= 32 &&
      typeof key.mod === 'boolean' && typeof key.shift === 'boolean' &&
      typeof key.alt === 'boolean';
  }
  function matchKey(event) {
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      if (key.code !== event.code) continue;
      if (key.mod !== (event.metaKey || event.ctrlKey)) continue;
      if (key.shift !== event.shiftKey) continue;
      if (key.alt !== event.altKey) continue;
      return key.id;
    }
    return null;
  }
  // 抓取阶段先认出可能是宿主的那个键，等这一轮任务跑完再报：在那之前页面还能对
  // 同一个事件 preventDefault，而页面自己处理掉的键不归宿主。帧只报 id，不报键名。
  function forwardKey(event) {
    if (!hello || event.repeat || event.defaultPrevented) return;
    const id = matchKey(event);
    if (id === null) return;
    const session = hello.sessionId;
    setTimeout(() => {
      if (event.defaultPrevented || !hello || session !== hello.sessionId) return;
      window.parent.postMessage({
        channel: 'cheese-preview-runtime', version: 1,
        sessionId: session, type: 'key', id: id,
      }, hello.origin);
    }, 0);
  }
  window.addEventListener('keydown', forwardKey, true);
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window.parent || event.origin !== __PLATFORM_ORIGIN__ ||
        !data || data.channel !== 'cheese-preview-runtime' || data.version !== 1 ||
        data.type !== 'hello' || typeof data.sessionId !== 'string' ||
        data.sessionId.length > 128) return;
    hello = { sessionId: data.sessionId, origin: event.origin };
    // 键表随握手过来，最多 16 条；不认识的条目丢掉，不认识的键就不报。
    keys = Array.isArray(data.keys) ? data.keys.filter(usableKey).slice(0, 16) : [];
    publish();
  });
  window.CheesePreviewRuntime = Object.freeze({
    ready() { state = { type: 'ready' }; publish(); },
    error(message) {
      state = { type: 'error', message: String(message).slice(0, 1000) };
      publish();
    },
  });
  // The app can load this script after the parent's navigation-load hello.
  // Request that document's existing session; this is not a readiness signal.
  window.parent.postMessage({
    channel: 'cheese-preview-runtime', version: 1, type: 'hello-request',
  }, __PLATFORM_ORIGIN__);
})();
"""
