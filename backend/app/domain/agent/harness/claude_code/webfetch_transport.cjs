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
  const fail = responseFailures.get(args[0]);
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
