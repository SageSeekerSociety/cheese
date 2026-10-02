// The backend's side of a document: where it is loaded from and stored to
// (backend/app/api/routes/living_docs.py, the internal router).

export interface Loaded {
  /** The stored Yjs state, base64; null for a document never opened live. */
  state: string | null
  /** The stored Markdown — what a document never opened live is built from. */
  content: string
  doc_version: number
}

export interface StoreBody {
  state: Uint8Array
  /** null: store the state alone. */
  content: string | null
  actors: string[]
  operation?: Record<string, unknown> | null
  /** The first conversion from Markdown: recorded without a conversation event. */
  converted?: boolean
}

/** The backend's error envelope (backend/app/core/errors.py). */
interface ErrorBody {
  error?: { message?: string }
}

/** The backend refused or failed a store; `body` is its answer. */
export class BackendError extends Error {
  constructor(
    readonly status: number,
    readonly body: ErrorBody | null
  ) {
    super(`backend answered ${status}: ${body?.error?.message ?? ''}`)
  }

  /** What the backend said, in its own words. */
  get reason(): string | undefined {
    return this.body?.error?.message
  }
}

export class Backend {
  constructor(
    private readonly baseUrl: string,
    private readonly bearer: string
  ) {}

  private url(name: string): string {
    return `${this.baseUrl.replace(/\/$/, '')}/internal/collab/documents/${encodeURIComponent(name)}`
  }

  async load(name: string): Promise<Loaded> {
    const response = await fetch(this.url(name), { headers: { Authorization: this.bearer } })
    if (!response.ok) throw new BackendError(response.status, await answer(response))
    return (await response.json()) as Loaded
  }

  /** Store the document; answers with what the backend recorded. */
  async store(name: string, body: StoreBody): Promise<Record<string, unknown>> {
    const response = await fetch(this.url(name), {
      method: 'PUT',
      headers: { Authorization: this.bearer, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, state: Buffer.from(body.state).toString('base64') }),
    })
    if (!response.ok) throw new BackendError(response.status, await answer(response))
    return (await response.json()) as Record<string, unknown>
  }
}

async function answer(response: Response): Promise<ErrorBody | null> {
  try {
    return (await response.json()) as ErrorBody
  } catch {
    return null
  }
}
