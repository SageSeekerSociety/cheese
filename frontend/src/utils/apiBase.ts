// The admin model form's check on a runtime model's upstream address, the same
// rule as the backend's `api_base_allowed` (backend/app/domain/agent/schemas.py),
// run here only to save a round trip: https, or exactly the metering proxy's
// ChatGPT entry on the private network it shares with the gateway.
const METER_CHATGPT_BASE = /^http:\/\/metering-proxy:8445\/chatgpt\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\/?$/

/** Empty means the upstream's default endpoint and is allowed. */
export function apiBaseAllowed(value: string): boolean {
  const v = value.trim()
  return v === '' || v.startsWith('https://') || METER_CHATGPT_BASE.test(v)
}
