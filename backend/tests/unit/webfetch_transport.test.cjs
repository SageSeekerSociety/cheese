const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { Writable } = require('node:stream');
const zlib = require('node:zlib');
const { test } = require('node:test');
require('../../app/domain/agent/harness/claude_code/webfetch_transport.cjs');

const userAgent = 'Claude-User (claude-code/2.1.261; +https://support.anthropic.com/)';

async function serve(t, handler) {
  const server = http.createServer(handler);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}`;
}

function fetchBody(url, agent = userAgent) {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { headers: { 'User-Agent': agent } }, response => {
      const chunks = [];
      const destination = new Writable({
        write(chunk, encoding, callback) { chunks.push(chunk); callback(); },
      });
      destination.on('finish', () => resolve(Buffer.concat(chunks)));
      const stages = [response];
      if (response.headers['content-encoding'] === 'gzip') stages.push(zlib.createGunzip());
      // Use the patched export, like the native bundle after preload.
      require('node:stream').pipeline(...stages, destination, () => {});
    });
    request.on('error', reject);
    request.end();
  });
}

test('WebFetch receives the entire compressed body with keep-alive', { timeout: 2000 }, async t => {
  const body = Buffer.from('A complete webpage.\n'.repeat(10000));
  const url = await serve(t, (request, response) => {
    assert.equal(request.headers.connection, 'keep-alive');
    response.writeHead(200, { 'Content-Encoding': 'gzip' });
    response.end(zlib.gzipSync(body));
  });
  assert.deepEqual(await fetchBody(url), body);
});

test('a mislabeled compressed body rejects after the request is destroyed', { timeout: 2000 }, async t => {
  const url = await serve(t, (request, response) => {
    response.writeHead(200, { 'Content-Encoding': 'gzip', Connection: 'close' });
    response.end('<html>This is not gzip.</html>');
  });
  await assert.rejects(fetchBody(url), /header|gzip|decompress/i);
});

test('an interrupted compressed response rejects instead of waiting', { timeout: 2000 }, async t => {
  const url = await serve(t, (request, response) => {
    response.writeHead(200, { 'Content-Encoding': 'gzip' });
    response.write(zlib.gzipSync('partial content').subarray(0, 12));
    setImmediate(() => response.destroy());
  });
  await assert.rejects(fetchBody(url), /aborted|reset|hang up|closed/i);
});

test('API requests retain their caller-specified connection policy', { timeout: 2000 }, async t => {
  const url = await serve(t, (request, response) => {
    assert.equal(request.headers.connection, 'close');
    response.end('API response');
  });
  await new Promise((resolve, reject) => {
    const request = http.request(url, {
      headers: { 'User-Agent': 'claude-cli/2.1.261', Connection: 'close' },
    }, response => {
      response.resume();
      response.on('end', resolve);
    });
    request.on('error', reject);
    request.end();
  });
});
