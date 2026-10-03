// A request answered as server-sent events: one POST, then each `event:` /
// `data:` pair handed on as it arrives. The 芝士 on the task page and the one in
// a room's document both answer this way.
//
// A refusal comes before the stream starts, as an ordinary JSON error body; it
// rejects with `StreamRefused`, carrying that body for the caller to word.
import { authToken, BASE, ensureFreshToken, refreshNow } from '@/api'

export class StreamRefused extends Error {
  constructor(
    readonly status: number,
    readonly body: { message?: string } & Record<string, unknown>
  ) {
    super(body.message || `HTTP ${status}`)
  }
}

/** A refusal's body. The route answers JSON; one raised before it (an expired
 *  sign-in, a malformed request) answers this request's `Accept:
 *  text/event-stream` with a single `event: error` frame whose data is
 *  `{ message, i18n }`. Either way it comes back shaped like an API error body,
 *  so `refusalText` words it in the reader's language. */
async function refusedBody(res: Response): Promise<StreamRefused['body']> {
  const text = await res.text().catch(() => '')
  try {
    return JSON.parse(text) as StreamRefused['body']
  } catch {
    const data = /^data: (.*)$/m.exec(text)?.[1]
    try {
      const frame = (data ? JSON.parse(data) : {}) as { message?: string; i18n?: unknown }
      return { message: frame.message, error: { i18n: frame.i18n } }
    } catch {
      return {}
    }
  }
}

export async function postEventStream(
  path: string,
  body: unknown,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  options: { signal?: AbortSignal; onOpen?: () => void } = {}
): Promise<void> {
  await ensureFreshToken()
  const send = () =>
    fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(authToken() ? { Authorization: `Bearer ${authToken()}` } : {}),
      },
      body: JSON.stringify(body),
      signal: options.signal,
    })
  let res = await send()
  if (res.status === 401) {
    await refreshNow()
    res = await send()
  }
  if (!res.ok || !res.body) throw new StreamRefused(res.status, await refusedBody(res))
  options.onOpen?.()
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut: number
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const event = /^event: (.+)$/m.exec(raw)?.[1]
      const data = /^data: (.*)$/m.exec(raw)?.[1]
      if (event && data !== undefined) onEvent(event, JSON.parse(data) as Record<string, unknown>)
    }
  }
}
