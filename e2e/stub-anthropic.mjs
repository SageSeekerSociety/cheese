// Local fixture for the Anthropic subscription API. Like Cheese's metering proxy, it uses
// HTTPS CONNECT and a CA trusted through NODE_EXTRA_CA_CERTS.
// Run `node e2e/stub-anthropic.mjs` with openssl on PATH and export the printed
// HTTPS_PROXY and NODE_EXTRA_CA_CERTS. Use a fresh CLAUDE_CONFIG_DIR and run
// `claude auth login --claudeai`. Fetch the authorize URL and paste the
// code from the callback page into the CLI.
// curl -L --proxy "$HTTPS_PROXY" --cacert "$NODE_EXTRA_CA_CERTS" '<authorize URL>'
//
// Set STUB_ANTHROPIC_PLAN=max (default) or pro. Set STUB_ANTHROPIC_RATE_LIMIT to
// allowed (default), allowed_warning, or rejected. STUB_ANTHROPIC_TOOL is a
// JSON object with name and input. When the request lists that tool in tools,
// the stub requests it and returns a fixed greeting after a tool_result.
// STUB_ANTHROPIC_EXPIRES_IN sets the initial token lifetime in seconds.
// STUB_ANTHROPIC_RATE_LIMIT_AFTER_REFRESH changes the limit for the new token.
// STUB_ANTHROPIC_METER takes a placeholder, project, topic, and optional noLoginAnswers.
// It serves the production control table before forwarding to the simulated Anthropic API.
// Without it, OAuth and model requests use the simulated Anthropic account.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createSecureContext, TLSSocket } from 'node:tls';

const port = Number(process.env.STUB_ANTHROPIC_PORT ?? 4011);
const plan = process.env.STUB_ANTHROPIC_PLAN ?? 'max';
const rateLimit = process.env.STUB_ANTHROPIC_RATE_LIMIT ?? 'allowed';
const rateLimitAfterRefresh = process.env.STUB_ANTHROPIC_RATE_LIMIT_AFTER_REFRESH ?? rateLimit;
const initialExpiry = Number(process.env.STUB_ANTHROPIC_EXPIRES_IN ?? 3600);
const tool = JSON.parse(process.env.STUB_ANTHROPIC_TOOL ?? 'null');
const meter = JSON.parse(process.env.STUB_ANTHROPIC_METER ?? 'null');
const controlRows = meter ? JSON.parse(readFileSync(
  new URL('../deploy/metering-proxy/control_answers.json', import.meta.url),
  'utf8',
)).rows : [];
const defaultAnswerHosts = ['api.anthropic.com'];
if (!['max', 'pro'].includes(plan)) throw new Error('STUB_ANTHROPIC_PLAN must be max or pro');

const levels = { allowed: 0.2, allowed_warning: 0.95, rejected: 1 };
if (![rateLimit, rateLimitAfterRefresh].every((level) => Object.hasOwn(levels, level))) {
  throw new Error('Invalid subscription rate-limit status');
}
if (!Number.isSafeInteger(initialExpiry) || initialExpiry < 0) {
  throw new Error('STUB_ANTHROPIC_EXPIRES_IN must be a nonnegative integer');
}

const accessToken = 'stub-access-token';
const refreshToken = 'stub-refresh-token';
const refreshedToken = 'stub-refreshed-access-token';
const platformToken = 'stub-platform-access-token';
const authorizationCode = 'stub-authorization-code';
const scopes = 'user:profile user:inference';
const hosts = new Set([
  'api.anthropic.com', 'claude.com', 'claude.ai', 'platform.claude.com', 'console.anthropic.com',
  ...controlRows.flatMap((row) => row.hosts ?? defaultAnswerHosts),
]);
const reply = 'Hi there!';
const fiveHours = 5 * 3600;
const sevenDays = 7 * 24 * 3600;
const profile = {
  account: { uuid: '00000000-0000-4000-8000-000000000001', email: 'subscriber@example.invalid' },
  organization: {
    uuid: '00000000-0000-4000-8000-000000000002',
    name: 'Stub Subscription',
    organization_type: `claude_${plan}`,
    rate_limit_tier: plan === 'max' ? 'default_claude_max_5x' : 'default_claude_pro',
    has_extra_usage_enabled: false,
  },
};

function limits(rateLimit) {
  const now = Math.floor(Date.now() / 1000);
  return {
    'anthropic-ratelimit-unified-status': rateLimit,
    'anthropic-ratelimit-unified-representative-claim': 'five_hour',
    'anthropic-ratelimit-unified-reset': now + fiveHours,
    'anthropic-ratelimit-unified-5h-status': rateLimit,
    'anthropic-ratelimit-unified-5h-utilization': levels[rateLimit],
    'anthropic-ratelimit-unified-5h-reset': now + fiveHours,
    'anthropic-ratelimit-unified-7d-status': 'allowed',
    'anthropic-ratelimit-unified-7d-utilization': 0.25,
    'anthropic-ratelimit-unified-7d-reset': now + sevenDays,
    ...(rateLimit === 'allowed_warning' && {
      'anthropic-ratelimit-unified-5h-surpassed-threshold': 0.9,
    }),
  };
}

function json(response, status, body, headers = {}) {
  response.writeHead(status, { 'content-type': 'application/json', ...headers });
  response.end(JSON.stringify(body));
}

function error(response, status, type, message, headers) {
  json(response, status, { type: 'error', error: { type, message }, request_id: 'req_stub' }, {
    'request-id': 'req_stub', ...headers,
  });
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const data = Buffer.concat(chunks).toString('utf8');
  if (request.headers['content-type']?.startsWith('application/x-www-form-urlencoded')) {
    return Object.fromEntries(new URLSearchParams(data));
  }
  return JSON.parse(data);
}

function fillControlBody(value) {
  if (Array.isArray(value)) return value.map(fillControlBody);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, fillControlBody(item)]));
  }
  if (value === '{project}') return meter.project;
  if (value === '{topic}') return meter.topic;
  return value;
}

function controlAnswer(host, path) {
  return controlRows.find((row) => {
    if (!(row.hosts ?? defaultAnswerHosts).includes(host)) return false;
    if (Object.hasOwn(row, 'exact')) return path === row.exact;
    if (Object.hasOwn(row, 'prefix')) return path.startsWith(row.prefix);
    return true;
  });
}

function meterRequest(request, response, route, url) {
  const host = request.socket.tunnelHost;
  const path = request.url.split('?', 1)[0];
  const row = controlAnswer(host, path);
  if (row) {
    response.source = 'table';
    response.writeHead(row.status, { 'content-type': 'application/json' });
    response.end(row.body === null ? '' : JSON.stringify(fillControlBody(row.body)));
    return;
  }
  if (request.headers.authorization === `Bearer ${meter.placeholder}`) {
    if (meter.noLoginAnswers) {
      if (host === 'api.anthropic.com' && Object.hasOwn(meter.noLoginAnswers, path)) {
        response.source = 'no-login';
        response.writeHead(200, { 'content-type': 'application/json' });
        return response.end(meter.noLoginAnswers[path]);
      }
      response.source = 'no-credential';
      return error(response, 400, 'invalid_request_error',
        "cheese: the platform has no Claude login, so this project's subscription model cannot be served");
    }
    request.headers.authorization = `Bearer ${platformToken}`;
    console.log(`Upstream bearer ***${request.headers.authorization.slice(-4)} (placeholder ***${meter.placeholder.slice(-4)})`);
  }
  response.source = 'upstream';
  return handle(request, response, route, url);
}

function tokenGrant(response, body) {
  const refreshing = body?.grant_type === 'refresh_token';
  const valid = refreshing ? body.refresh_token === refreshToken
    : body?.grant_type === 'authorization_code' && body.code === authorizationCode;
  if (!valid) return json(response, 400, { error: 'invalid_grant' }, { 'cache-control': 'no-store' });
  console.log(`OAuth ${body.grant_type} -> issued access token`);
  json(response, 200, {
    access_token: refreshing ? refreshedToken : accessToken,
    refresh_token: refreshToken,
    token_type: 'Bearer',
    expires_in: refreshing ? 3600 : initialExpiry,
    scope: body.scope ?? scopes,
    account: profile.account,
    organization: profile.organization,
  }, { 'cache-control': 'no-store' });
}

function authorize(response, url) {
  const state = url.searchParams.get('state');
  const callback = URL.parse(url.searchParams.get('redirect_uri') ?? '');
  if (!state || callback?.origin !== 'https://platform.claude.com'
    || callback.pathname !== '/oauth/code/callback') {
    return error(response, 400, 'invalid_request_error', 'Expected state and the manual callback URL');
  }
  callback.search = new URLSearchParams({ code: authorizationCode, state }).toString();
  response.writeHead(302, { location: callback.href, 'cache-control': 'no-store' }).end();
}

function callback(response, url) {
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  if (code !== authorizationCode || !/^[\w-]+$/.test(state ?? '')) {
    return error(response, 400, 'invalid_request_error', 'Invalid authorization code');
  }
  response.writeHead(200, { 'content-type': 'text/html', 'cache-control': 'no-store' });
  response.end(`<!doctype html><p>Paste this code into your terminal.</p><code>${code}#${state}</code>`);
}

function messageFor(body) {
  const choice = body.tool_choice;
  const lastContent = body.messages.at(-1)?.content;
  const hasResult = Array.isArray(lastContent) && lastContent.some((block) => block?.type === 'tool_result');
  const toolAvailable = tool && Array.isArray(body.tools) && body.tools.some((offer) => offer?.name === tool.name);
  const useTool = !hasResult && toolAvailable && choice?.type !== 'none'
    && (choice?.type !== 'tool' || choice.name === tool.name);
  return {
    id: 'msg_stub',
    type: 'message',
    role: 'assistant',
    model: body.model,
    content: useTool
      ? [{ type: 'tool_use', id: 'toolu_stub', name: tool.name, input: tool.input }]
      : [{ type: 'text', text: reply }],
    stop_reason: useTool ? 'tool_use' : 'end_turn',
    stop_sequence: null,
    usage: { input_tokens: 12, output_tokens: 8 },
  };
}

function streamMessage(response, message, headers) {
  response.writeHead(200, { ...headers, 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  function emit(type, data = {}) {
    response.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`);
  }
  emit('message_start', {
    message: { ...message, content: [], stop_reason: null, usage: { ...message.usage, output_tokens: 0 } },
  });
  const [block] = message.content;
  const isTool = block.type === 'tool_use';
  emit('content_block_start', {
    index: 0,
    content_block: isTool ? { ...block, input: {} } : { type: 'text', text: '' },
  });
  emit('content_block_delta', {
    index: 0,
    delta: isTool
      ? { type: 'input_json_delta', partial_json: JSON.stringify(block.input) }
      : { type: 'text_delta', text: block.text },
  });
  emit('content_block_stop', { index: 0 });
  emit('message_delta', {
    delta: { stop_reason: message.stop_reason, stop_sequence: null },
    usage: { output_tokens: message.usage.output_tokens },
  });
  emit('message_stop');
  response.end();
}

async function handle(request, response, route, url) {
  if (route === 'GET /cai/oauth/authorize' || route === 'GET /oauth/authorize') return authorize(response, url);
  if (route === 'GET /oauth/code/callback') return callback(response, url);
  if (route === 'POST /v1/oauth/token') return tokenGrant(response, await readBody(request));
  if (![
    'POST /v1/messages', 'POST /v1/messages/count_tokens',
    'GET /api/oauth/profile', 'GET /api/oauth/usage', 'GET /api/oauth/claude_cli/roles',
    'GET /api/claude_cli/bootstrap', 'GET /api/claude_code_penguin_mode', 'GET /api/oauth/validate',
  ].includes(route)) {
    return error(response, 404, 'not_found_error', 'Unknown endpoint');
  }

  const bearer = request.headers.authorization;
  const refreshed = bearer === `Bearer ${refreshedToken}`;
  if (!refreshed && bearer !== `Bearer ${accessToken}` && bearer !== `Bearer ${platformToken}`) {
    return error(response, 401, 'authentication_error', 'Invalid bearer token');
  }
  if (route === 'GET /api/oauth/profile') return json(response, 200, profile);
  if (route === 'GET /api/claude_cli/bootstrap' || route === 'GET /api/oauth/validate') return json(response, 200, {});
  if (route === 'GET /api/claude_code_penguin_mode') return json(response, 200, { enabled: false });
  if (route === 'GET /api/oauth/claude_cli/roles') {
    return json(response, 200, { organization_role: 'admin', workspace_role: null, organization_name: profile.organization.name });
  }
  const currentLimit = refreshed ? rateLimitAfterRefresh : rateLimit;
  const headers = limits(currentLimit);
  if (route === 'GET /api/oauth/usage') {
    return json(response, 200, Object.fromEntries([['five_hour', '5h'], ['seven_day', '7d']].map(
      ([name, window]) => [name, {
        utilization: Math.round(headers[`anthropic-ratelimit-unified-${window}-utilization`] * 100),
        resets_at: new Date(headers[`anthropic-ratelimit-unified-${window}-reset`] * 1000).toISOString(),
      }],
    )));
  }
  if (!request.headers['anthropic-beta']?.split(',').some((beta) => beta.trim() === 'oauth-2025-04-20')) {
    return error(response, 400, 'invalid_request_error', 'Missing OAuth beta header');
  }
  if (currentLimit === 'rejected' && route === 'POST /v1/messages') {
    return error(response, 429, 'rate_limit_error', 'You have reached your subscription rate limit.', {
      ...headers, 'retry-after': fiveHours,
    });
  }
  const body = await readBody(request);
  if (typeof body?.model !== 'string' || !Array.isArray(body.messages)) {
    return error(response, 400, 'invalid_request_error', 'Expected model and messages');
  }
  if (route === 'POST /v1/messages/count_tokens') return json(response, 200, { input_tokens: 12 }, headers);
  if (!Number.isInteger(body.max_tokens) || body.max_tokens < 1) {
    return error(response, 400, 'invalid_request_error', 'Expected positive integer max_tokens');
  }
  const message = messageFor(body);
  if (body.stream) return streamMessage(response, message, { ...headers, 'request-id': 'req_stub' });
  json(response, 200, message, { ...headers, 'request-id': 'req_stub' });
}

const api = createServer(async (request, response) => {
  const host = request.socket.tunnelHost;
  const url = URL.parse(request.url, `https://${host}`);
  const route = `${request.method} ${url?.pathname ?? request.url.split('?')[0]}`;
  const caller = request.headers.authorization === `Bearer ${meter?.placeholder}` ? 'placeholder' : 'other';
  response.on('finish', () => console.log(
    `${host} ${route} -> ${response.statusCode}${meter ? ` (${response.source ?? 'unrouted'}, ${caller})` : ''}`,
  ));
  try {
    if (url?.host !== host || ![host, `${host}:443`].includes(request.headers.host?.toLowerCase())) {
      return error(response, 421, 'invalid_request_error', 'Host does not match CONNECT');
    }
    if (meter) await meterRequest(request, response, route, url);
    else await handle(request, response, route, url);
  } catch (err) {
    if (response.headersSent) return response.destroy();
    if (err instanceof SyntaxError) return error(response, 400, 'invalid_request_error', 'Invalid JSON');
    console.error(err.name);
    error(response, 500, 'api_error', 'Stub could not complete request');
  }
});

const certDir = mkdtempSync(join(tmpdir(), 'cheese-anthropic-ca-'));
process.on('exit', () => rmSync(certDir, { recursive: true, force: true }));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => process.exit(0));
const caPath = join(certDir, 'ca.pem');
writeFileSync(join(certDir, 'cert.cnf'), `
[req]
distinguished_name = name
[name]
[ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${[...hosts].map((host) => `DNS:${host}`).join(',')}
`);
function openssl(...args) {
  execFileSync('openssl', args, { cwd: certDir, stdio: ['ignore', 'ignore', 'pipe'] });
}
openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '1', '-config',
  'cert.cnf', '-extensions', 'ca', '-subj', '/CN=Cheese Anthropic Test CA', '-keyout', 'ca.key', '-out', 'ca.pem');
openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-config', 'cert.cnf', '-subj',
  '/CN=api.anthropic.com', '-keyout', 'leaf.key', '-out', 'leaf.csr');
openssl('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-set_serial', '1',
  '-days', '1', '-sha256', '-extfile', 'cert.cnf', '-extensions', 'server', '-out', 'leaf.pem');
const secureContext = createSecureContext({
  key: readFileSync(join(certDir, 'leaf.key')),
  cert: readFileSync(join(certDir, 'leaf.pem')),
});

const proxy = createServer((request, response) => {
  console.log(`${request.method} without CONNECT -> 403`);
  response.writeHead(403).end();
});
proxy.on('connect', (request, socket, head) => {
  const authority = request.url.toLowerCase();
  const [host] = authority.split(':');
  if (!hosts.has(host) || authority !== `${host}:443`) {
    console.log(`CONNECT ${request.url} -> 403`);
    socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    return;
  }
  console.log(`CONNECT ${request.url} -> 200`);
  socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
  if (head.length) socket.unshift(head);
  const tls = new TLSSocket(socket, { isServer: true, secureContext, ALPNProtocols: ['http/1.1'] });
  tls.tunnelHost = host;
  tls.on('error', (err) => console.log(`TLS ${host} -> ${err.code}`));
  api.emit('connection', tls);
});
proxy.listen({ port, host: '127.0.0.1' }, function () {
  console.log(`HTTPS_PROXY=http://127.0.0.1:${this.address().port}`);
  console.log(`NODE_EXTRA_CA_CERTS=${caPath}`);
});
