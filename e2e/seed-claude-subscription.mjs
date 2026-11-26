// Write synthetic subscription credentials into a fresh CLAUDE_CONFIG_DIR.
// Claude Code 2.1.316 routes OAuth login and refresh to Anthropic even when
// ANTHROPIC_BASE_URL points to the stub. Its internal
// CLAUDE_CODE_CUSTOM_OAUTH_URL rejects loopback URLs.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = process.env.CLAUDE_CONFIG_DIR;
if (!dir) throw new Error('Set CLAUDE_CONFIG_DIR to a fresh test directory');
const base = new URL(process.env.ANTHROPIC_BASE_URL);
if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1') {
  throw new Error('Set ANTHROPIC_BASE_URL to the loopback stub');
}
const response = await fetch(new URL('/v1/oauth/token', base), {
  method: 'POST',
  body: new URLSearchParams({ grant_type: 'authorization_code', code: 'stub-authorization-code' }),
});
if (!response.ok) throw new Error(`Stub token endpoint returned HTTP ${response.status}`);
const token = await response.json();

mkdirSync(dir, { recursive: true, mode: 0o700 });
writeFileSync(join(dir, '.credentials.json'), JSON.stringify({
  claudeAiOauth: {
    accessToken: token.access_token,
    refreshToken: token.refresh_token,
    expiresAt: Date.now() + token.expires_in * 1000,
    scopes: token.scope.split(' '),
    subscriptionType: token.organization.organization_type.slice('claude_'.length),
    rateLimitTier: token.organization.rate_limit_tier,
  },
}), { mode: 0o600, flag: 'wx' });
