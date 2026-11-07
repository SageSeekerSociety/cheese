// 预览专用（不在 PR 里）：左下角的小条切换修前/修后两版，打开「演示替身」后，芝士对话接口
// /api/assistant/conversations* 与 /api/assistant/routines 换成页面里的假数据：一段对话 c1 正在答，
// 回答第一段之后人为停 8 秒再写完。其余接口照常走生产后端。
(function () {
  var build = (document.cookie.match(/cheese_build=(before|after)/) || [])[1] || 'after';
  var on = localStorage.getItem('cheeseRepro') === '1';
  var DELAY = 8000;
  if (on) {
    var real = window.fetch.bind(window);
    var env = function (data) { return new Response(JSON.stringify({ code: 200, message: 'ok', data: data }), { status: 200, headers: { 'content-type': 'application/json' } }); };
    var Q = { role: 'user', text: '帮我把这周还没做完的任务列一下', at: '2026-11-07T15:00:00Z', seq: 1 };
    var A1 = '这周还没做完的有三件：', A2 = '一、首页侧栏头像；二、采纳卡片宽度；三、文档浮动条。';
    var t0 = null;
    var finished = function () { return t0 !== null && Date.now() - t0 >= DELAY; };
    var topic = { id: 'c1', title: '这周的任务（演示替身）', lastActiveAt: '2026-11-07T15:00:00Z', questions: 1 };
    window.fetch = async function (input, init) {
      var url = typeof input === 'string' ? input : input.url;
      var method = ((init && init.method) || 'GET').toUpperCase();
      if (/\/api\/assistant\/routines$/.test(url)) return env({ limit: 20, routines: [{ id: 'r1', title: '盘点这周的任务', note: '盘点这周还没做完的任务（演示替身）', when: '等这周的任务盘点完', state: 'active', paused: '', nextRunAt: null, once: true, conversationId: 'c1', missing: [], trigger: '', spec: {}, timezone: 'Asia/Shanghai', lastRun: null, weekCredits: 0 }] });
      if (/\/api\/assistant\/conversations(\?.*)?$/.test(url) && method === 'GET') return env({ conversations: [topic], next: null });
      if (/\/api\/assistant\/conversations\/c1$/.test(url)) return env(Object.assign({}, topic, finished()
        ? { messages: [Q, { role: 'assistant', text: A1 + A2, at: '2026-11-07T15:00:09Z', seq: 2 }], answering: null }
        : { messages: [Q], answering: 'q1' }));
      if (/\/api\/assistant\/conversations\/c1\/answers\/q1/.test(url)) {
        if (t0 === null) t0 = Date.now();
        var enc = new TextEncoder();
        var frame = function (ev, data, id) { return enc.encode('id: ' + id + '\nevent: ' + ev + '\ndata: ' + JSON.stringify(data) + '\n\n'); };
        var body = new ReadableStream({ start: async function (c) {
          c.enqueue(frame('delta', { text: A1, at: 0 }, '1-0'));
          var wait = t0 + DELAY - Date.now();
          if (wait > 0) await new Promise(function (r) { setTimeout(r, wait); });
          c.enqueue(frame('delta', { text: A2, at: A1.length }, '2-0'));
          c.enqueue(frame('done', { stopped: false }, '3-0'));
          c.close();
        } });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
      }
      if (url.indexOf('/api/assistant/conversations') >= 0) return env({});
      return real(input, init);
    };
  }
  function bar() {
    var d = document.createElement('div');
    d.setAttribute('style', 'position:fixed;left:72px;bottom:8px;z-index:2147483647;background:#1f2328;color:#fff;font:12px/1.6 system-ui,sans-serif;padding:6px 10px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.3);max-width:620px');
    var b = function (label, fn) { var x = document.createElement('button'); x.textContent = label; x.setAttribute('style', 'margin-left:8px;background:#f59e0b;color:#111;border:0;border-radius:4px;padding:1px 8px;cursor:pointer;font:inherit'); x.onclick = fn; return x; };
    d.appendChild(document.createTextNode('PR #5196 预览 · 当前：' + (build === 'after' ? '修后 7241ff47f' : '修前 043455b5d') + ' · 演示替身：' + (on ? '开（芝士对话为假数据，回答中途停 8 秒）' : '关')));
    d.appendChild(b(build === 'after' ? '切到修前' : '切到修后', function () { document.cookie = 'cheese_build=' + (build === 'after' ? 'before' : 'after') + ';path=/'; location.reload(); }));
    d.appendChild(b(on ? '关掉替身' : '打开替身', function () { localStorage.setItem('cheeseRepro', on ? '0' : '1'); location.reload(); }));
    if (on) d.appendChild(b('去「设置 › 芝士」', function () { location.href = '/users/settings/assistant'; }));
    document.body.appendChild(d);
  }
  if (document.body) bar(); else document.addEventListener('DOMContentLoaded', bar);
})();
