// Loopback Anthropic API for tests using synthetic subscription OAuth tokens.
// Start with `node e2e/stub-anthropic.mjs` and export the printed ANTHROPIC_BASE_URL.
// With a fresh CLAUDE_CONFIG_DIR, run `node e2e/seed-claude-subscription.mjs`
// and then run Claude Code with those same two variables. Other HTTP clients
// send Authorization: Bearer stub-access-token and anthropic-beta: oauth-2025-04-20.
//
// Set STUB_ANTHROPIC_PLAN=max (default) or pro. Set STUB_ANTHROPIC_RATE_LIMIT to
// allowed (default), allowed_warning, or rejected. STUB_ANTHROPIC_TOOL is a
// JSON object with name and input. When the request lists that tool in tools,
// the stub requests it and returns a fixed greeting after a tool_result.
import { createServer } from 'node:http';

const port = Number(process.env.STUB_ANTHROPIC_PORT ?? 4011);
const plan = process.env.STUB_ANTHROPIC_PLAN ?? 'max';
const rateLimit = process.env.STUB_ANTHROPIC_RATE_LIMIT ?? 'allowed';
const tool = JSON.parse(process.env.STUB_ANTHROPIC_TOOL ?? 'null');
if (!['max', 'pro'].includes(plan)) throw new Error('STUB_ANTHROPIC_PLAN must be max or pro');

const levels = { allowed: 0.2, allowed_warning: 0.95, rejected: 1 };
if (!Object.hasOwn(levels, rateLimit)) throw new Error('Invalid STUB_ANTHROPIC_RATE_LIMIT');

const accessToken = 'stub-access-token';
const refreshToken = 'stub-refresh-token';
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

function limits() {
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

function tokenGrant(response, body) {
  const valid = (body?.grant_type === 'refresh_token' && body.refresh_token === refreshToken)
    || (body?.grant_type === 'authorization_code' && body.code === 'stub-authorization-code');
  if (!valid) return json(response, 400, { error: 'invalid_grant' }, { 'cache-control': 'no-store' });
  json(response, 200, {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'Bearer',
    expires_in: 3600,
    scope: 'user:profile user:inference',
    account: profile.account,
    organization: profile.organization,
  }, { 'cache-control': 'no-store' });
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

async function handle(request, response, route) {
  if (route === 'POST /v1/oauth/token') return tokenGrant(response, await readBody(request));
  if (![
    'POST /v1/messages', 'POST /v1/messages/count_tokens',
    'GET /api/oauth/profile', 'GET /api/oauth/usage', 'GET /api/oauth/claude_cli/roles',
  ].includes(route)) {
    return error(response, 404, 'not_found_error', 'Unknown endpoint');
  }

  if (request.headers.authorization !== `Bearer ${accessToken}`) {
    return error(response, 401, 'authentication_error', 'Invalid bearer token');
  }
  if (route === 'GET /api/oauth/profile') return json(response, 200, profile);
  if (route === 'GET /api/oauth/claude_cli/roles') {
    return json(response, 200, { organization_role: 'admin', workspace_role: null, organization_name: profile.organization.name });
  }
  const headers = limits();
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
  if (rateLimit === 'rejected' && route === 'POST /v1/messages') {
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

createServer(async (request, response) => {
  const route = `${request.method} ${request.url.split('?')[0]}`;
  response.on('finish', () => console.log(`${route} -> ${response.statusCode}`));
  try {
    await handle(request, response, route);
  } catch (err) {
    if (response.headersSent) return response.destroy();
    if (err instanceof SyntaxError) return error(response, 400, 'invalid_request_error', 'Invalid JSON');
    console.error(err.name);
    error(response, 500, 'api_error', 'Stub could not complete request');
  }
}).listen(port, '127.0.0.1', function () {
  console.log(`ANTHROPIC_BASE_URL=http://127.0.0.1:${this.address().port}`);
});
