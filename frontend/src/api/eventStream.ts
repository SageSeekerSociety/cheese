// A request answered as server-sent events: one POST, then each `event:` /
// `data:` pair handed on as it arrives. The 芝士 on the task page and the one in
// a room's document both answer this way. The answer is read to its end on the
// server whatever happens to the connection, so a broken stream is read on
// from where it broke (`followEventStream`).
//
// A refusal comes before the stream starts, as an ordinary JSON error body; it
// rejects with `StreamRefused`, carrying that body for the caller to word.
import { authToken, BASE, ensureFreshToken, refreshNow } from '@/api'
// 它抛在流的这一层，接在文档助手那一层——那层不许够得着接口层，所以这个信号本身
// 住在 lib 里。见 lib/streamCut.ts。
import { StreamCut } from '@/lib/streamCut'

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
 *  text/event-stream` with a single `event: error` frame whose data is that
 *  same error body (`backend/app/core/errors.py`, `_event_error`). Either way
 *  it comes back shaped like an API error body, so `refusalText` words it in
 *  the reader's language and a caller can switch on `error.name`. */
async function refusedBody(res: Response): Promise<StreamRefused['body']> {
  const text = await res.text().catch(() => '')
  try {
    return JSON.parse(text) as StreamRefused['body']
  } catch {
    const data = /^data: (.*)$/m.exec(text)?.[1]
    try {
      return (data ? JSON.parse(data) : {}) as StreamRefused['body']
    } catch {
      return {}
    }
  }
}

/** One event of a stream; `id` is its place in the stream, when the stream
 *  gives one, to read on from it. */
export type OnEvent = (event: string, data: Record<string, unknown>, id?: string) => void

export async function postEventStream(
  path: string,
  body: unknown,
  onEvent: OnEvent,
  options: { signal?: AbortSignal; onOpen?: () => void } = {}
): Promise<void> {
  await open(path, { method: 'POST', body: JSON.stringify(body), signal: options.signal }, onEvent, options.onOpen)
}

/** A stream read with GET: the rest of an answer, from where its reader left it. */
export async function getEventStream(path: string, onEvent: OnEvent, signal?: AbortSignal): Promise<void> {
  await open(path, { method: 'GET', signal }, onEvent)
}

async function open(
  path: string,
  init: { method: string; body?: string; signal?: AbortSignal },
  onEvent: OnEvent,
  onOpen?: () => void
): Promise<void> {
  await ensureFreshToken()
  const send = () =>
    fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        Accept: 'text/event-stream',
        ...(authToken() ? { Authorization: `Bearer ${authToken()}` } : {}),
      },
    })
  let res = await send()
  if (res.status === 401) {
    await refreshNow()
    res = await send()
  }
  if (!res.ok || !res.body) throw new StreamRefused(res.status, await refusedBody(res))
  onOpen?.()
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
      const id = /^id: (.+)$/m.exec(raw)?.[1]
      if (event && data !== undefined) onEvent(event, JSON.parse(data) as Record<string, unknown>, id)
    }
  }
}

/** How many times, and how far apart, a reader tries to read on after its
 *  stream broke. A backend that went away mid-answer hands the question to the
 *  next one within half a minute (`session_host/consumptions.py`). */
const READ_ON_TRIES = 8
const READ_ON_WAIT_MS = 5_000

/** Read a question's answer to its end, across broken connections. `start`
 *  opens the stream (the POST that asks, or a GET that reads on); the question
 *  says its id first (`answering`), and every event after it carries its
 *  place. A stream that ends before one of `endsOn` is read on from the last
 *  place seen (`readOn`), until it ends or the tries run out (`StreamCut`). */
export async function followEventStream(
  start: (onEvent: OnEvent) => Promise<void>,
  readOn: (question: string, after: string) => string,
  onEvent: OnEvent,
  options: { signal?: AbortSignal; endsOn?: string[]; question?: string } = {}
): Promise<void> {
  const endsOn = options.endsOn ?? ['done', 'error']
  let question = options.question ?? null
  let after = '0'
  let ended = false
  const seen: OnEvent = (event, data, id) => {
    if (event === 'answering' && typeof data.id === 'string') question = data.id
    if (id) after = id
    if (endsOn.includes(event)) ended = true
    onEvent(event, data, id)
  }
  try {
    await start(seen)
  } catch (error) {
    if (error instanceof StreamRefused || options.signal?.aborted || !question) throw error
  }
  for (let tries = 0; !ended; tries++) {
    if (!question || tries >= READ_ON_TRIES) throw new StreamCut()
    await wait(tries ? READ_ON_WAIT_MS : 500, options.signal)
    try {
      await getEventStream(readOn(question, after), seen, options.signal)
    } catch (error) {
      if (options.signal?.aborted) throw error
      if (error instanceof StreamRefused && error.status === 404) throw new StreamCut()
    }
  }
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason)
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(signal.reason)
    })
  })
}
