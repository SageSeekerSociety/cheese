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

// A private chat's page reads leave through its egress proxy, which refuses
// every internal address (`remote_execution/private_egress.py`). Its address is
// CHEESE_WEBFETCH_EGRESS; when it is set, every WebFetch request is tunnelled
// there with CONNECT, whatever proxy agent the CLI built for HTTPS_PROXY (the
// metering proxy, whose own exit reaches the host's network). The agent is our
// own rather than a copy of the CLI's: measured on 2.1.299 a request that the
// session gives no HTTPS_PROXY carries no proxy agent at all.
const net = require('node:net');
const tls = require('node:tls');
const egress = process.env.CHEESE_WEBFETCH_EGRESS;

function egressAgent(secure) {
  const proxy = new URL(egress);
  const agent = new (secure ? https.Agent : http.Agent)({ keepAlive: false });
  agent.createConnection = function (options, callback) {
    const host = options.host || options.hostname;
    const port = options.port || (secure ? 443 : 80);
    const authority = `${host.includes(':') ? `[${host}]` : host}:${port}`;
    const raw = net.connect(Number(proxy.port), proxy.hostname);
    raw.once('error', callback);
    raw.once('connect', () => {
      raw.write(`CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n\r\n`);
    });
    let head = Buffer.alloc(0);
    const onData = chunk => {
      head = Buffer.concat([head, chunk]);
      const end = head.indexOf('\r\n\r\n');
      if (end < 0) return;
      raw.removeListener('data', onData);
      const [status] = head.subarray(0, end).toString().split('\r\n');
      if (status.split(' ')[1] !== '200') {
        raw.destroy();
        const reason = head.subarray(end + 4).toString().trim();
        return callback(new Error(`The private chat's egress refused ${authority}: ${reason || status}`));
      }
      if (!secure) return callback(null, raw);
      const secured = tls.connect({ socket: raw, servername: host, ALPNProtocols: ['http/1.1'] });
      secured.once('secureConnect', () => callback(null, secured));
      secured.once('error', callback);
    };
    raw.on('data', onData);
  };
  return agent;
}

const egressAgents = egress ? { http: egressAgent(false), https: egressAgent(true) } : null;

function isPageRequest(args) {
  const options = args.find(arg => arg && typeof arg === 'object' && !(arg instanceof URL));
  const headers = (options && options.headers) || {};
  return Object.keys(headers).some(
    name => name.toLowerCase() === 'user-agent' && String(headers[name]).startsWith('Claude-User (')
  );
}

for (const protocol of [http, https]) {
  const originalRequest = protocol.request;
  protocol.request = function (...args) {
    if (egressAgents && isPageRequest(args)) {
      const at = args.findIndex(arg => arg && typeof arg === 'object' && !(arg instanceof URL));
      args[at] = { ...args[at], agent: egressAgents[protocol === https ? 'https' : 'http'] };
    }
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
