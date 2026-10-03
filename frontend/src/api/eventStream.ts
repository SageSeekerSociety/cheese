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
  if (!res.ok || !res.body) {
    const refused = (await res.json().catch(() => ({}))) as StreamRefused['body']
    throw new StreamRefused(res.status, refused)
  }
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
