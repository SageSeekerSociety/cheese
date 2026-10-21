// A private chat's WebFetch reads pages only through the chat's egress proxy.
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const { once } = require('node:events');
const { test } = require('node:test');

const userAgent = 'Claude-User (claude-code/2.1.299; +https://support.anthropic.com/)';
const tunnelled = [];

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

// Stands in for `private_egress.py`: tunnels what it allows, refuses the rest
// with a reason, and records every CONNECT it is asked for.
const ready = (async () => {
  const site = http.createServer((request, response) => response.end('PAGE'));
  const sitePort = await listen(site);
  const egress = net.createServer(client => {
    client.once('data', head => {
      const target = head.toString().split(' ')[1];
      tunnelled.push(target);
      if (target.startsWith('refused.example')) {
        client.end('HTTP/1.1 403 Forbidden\r\ncontent-length: 29\r\n\r\nrefused.example is not public');
        return;
      }
      const upstream = net.connect(sitePort, '127.0.0.1', () => {
        client.write('HTTP/1.1 200 Connection established\r\n\r\n');
        client.pipe(upstream).pipe(client);
      });
    });
  });
  process.env.CHEESE_WEBFETCH_EGRESS = `http://127.0.0.1:${await listen(egress)}`;
  require('../../app/domain/agent/harness/claude_code/webfetch_transport.cjs');
  return { sitePort, close: () => { site.closeAllConnections(); site.close(); egress.close(); } };
})();

function read(url, agent = userAgent) {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { headers: { 'User-Agent': agent } }, response => {
      let body = '';
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve(body));
    });
    request.on('error', reject);
    request.end();
  });
}

test.after(async () => (await ready).close());

test('a page read leaves through the egress proxy', { timeout: 3000 }, async () => {
  const { sitePort } = await ready;
  tunnelled.length = 0;
  assert.equal(await read(`http://page.example:${sitePort}/`), 'PAGE');
  assert.deepEqual(tunnelled, [`page.example:${sitePort}`]);
});

test('a read the egress refuses fails with its reason', { timeout: 3000 }, async () => {
  await ready;
  await assert.rejects(read('http://refused.example/'), /egress refused refused\.example:80: refused\.example is not public/);
});

test("the CLI's own requests do not go through the egress", { timeout: 3000 }, async () => {
  const { sitePort } = await ready;
  tunnelled.length = 0;
  assert.equal(await read(`http://127.0.0.1:${sitePort}/`, 'claude-cli/2.1.299'), 'PAGE');
  assert.deepEqual(tunnelled, []);
});
