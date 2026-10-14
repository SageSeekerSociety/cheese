// Claude Code 2.1.261 drops response/decompression errors after req.destroyed.
// Forward them to its request-error handler, which still rejects the fetch.
const http = require('node:http');
const https = require('node:https');
const stream = require('node:stream');
const { syncBuiltinESMExports } = require('node:module');

const responseFailures = new WeakMap();
const originalPipeline = stream.pipeline;
stream.pipeline = function (...args) {
  const output = originalPipeline.apply(this, args);
  const input = Array.isArray(args[0]) ? args[0][0] : args[0];
  const fail = responseFailures.get(input);
  if (fail) output.on('error', fail);
  return output;
};

for (const protocol of [http, https]) {
  const originalRequest = protocol.request;
  protocol.request = function (...args) {
    const request = originalRequest.apply(this, args);
    if (!String(request.getHeader('user-agent')).startsWith('Claude-User (')) {
      return request;
    }
    // Request keep-alive to mitigate the observed TLS failure on the cloud route
    // during server-initiated close. TLS settings remain unchanged.
    request.setHeader('Connection', 'keep-alive');
    let failed = false;
    request.on('error', () => { failed = true; });
    const fail = error => {
      if (failed) return;
      failed = true;
      request.emit('error', error);
    };
    request.prependOnceListener('response', response => {
      responseFailures.set(response, fail);
      response.once('error', fail);
    });
    return request;
  };
}
syncBuiltinESMExports();

// The pinned harness resolves an explicit Agent model before its native child
// default. Carry that resolved choice to admission without buffering the growing
// request body in the metering proxy. Admission still validates supply/policy.
//
// 2.1.283 took the harness's own model requests off this path and below JS
// altogether — a whole 2.1.299 turn calls `fetch` zero times (measured
// 2026-10-14) — so no build past 2.1.282 gets this header, and nothing
// downstream misses it: with no `x-cheese-child-model` the metering
// proxy buffers a subagent's `/v1/messages` and reads the model off the request
// body, which is where the harness put it all along. A subagent therefore still
// binds to the model its parent named rather than to the child default. Kept
// for the builds whose requests really do arrive here.
//
// The node:http patch above is NOT in that position: on 2.1.299 the WebFetch
// page request still leaves through `node:https` with the `Claude-User (` UA
// (same measurement), so its keep-alive and its forwarding of a dropped
// response error are still load-bearing.
const originalFetch = globalThis.fetch;
globalThis.fetch = function(input, init) {
  const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
  const requestClass = headers.get('x-claude-code-request-class');
  if (headers.get('x-claude-code-agent-id') || requestClass === 'subagent' || requestClass === 'workflow') {
    if (typeof init?.body === 'string') {
      const body = JSON.parse(init.body);
      if (typeof body.model === 'string') headers.set('x-cheese-child-model', body.model);
    }
  }
  return originalFetch.call(this, input, {...init, headers});
};
