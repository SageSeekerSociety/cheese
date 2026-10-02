import { t } from '@/i18n'

// A 429 from the backend's per-client limits (backend `app/core/request_limits.py`).
// Other routes answer 429 for reasons of their own — an assistant still
// answering the last question — and say so in their own words.
const QUOTA_EXCEEDED = 'https://iana.org/assignments/http-problem-types#quota-exceeded'

// The longest a read waits before its one retry. A GET runs inside
// `READ_BUDGET_MS` (20 s), and the backend may already have held it in line
// for up to 15 s, so a longer wait would only end in the budget's timeout.
const MAX_WAIT_S = 10

// `Retry-After` in whole seconds: either form the header allows, or null.
export function retryAfterSeconds(res: Response): number | null {
  const raw = res.headers?.get('Retry-After')?.trim()
  if (!raw) return null
  if (/^\d+$/.test(raw)) return Number(raw)
  const at = Date.parse(raw)
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000))
}

// How long a refused read waits before asking again, or null when it should
// not: not a GET, no `Retry-After`, or a wait too long to be worth it.
export function rateLimitRetryMs(method: string, res: Response): number | null {
  if (res.status !== 429 || method.toUpperCase() !== 'GET') return null
  const seconds = retryAfterSeconds(res)
  return seconds == null || seconds > MAX_WAIT_S ? null : seconds * 1000
}

// What to tell the user when the per-client limit refused the request.
export function rateLimitedText(res: Response, body: unknown): string | null {
  if (res.status !== 429 || (body as { type?: unknown } | null)?.type !== QUOTA_EXCEEDED) return null
  return t('global.request.rateLimited', { seconds: retryAfterSeconds(res) ?? 1 })
}
