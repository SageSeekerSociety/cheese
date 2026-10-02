// The collaboration service: every room's living document, live.
//
// People edit a document here together over a WebSocket (Hocuspocus, Yjs). The
// backend keeps the record: this service loads a document from it, and stores
// it back — the Yjs state and the Markdown exported from it — a few seconds
// after the typing stops. That store is the ONLY way a new version reaches the
// backend, so a write that does not come from an editor (芝士's
// `cheese_doc_set`, a restore, an accepted AI proposal) comes here too, through
// `/internal/documents/:name/replace`, and becomes a change to the live
// document. Written to the database directly it would be overwritten by the
// next store.
//
// Three rules hold the record together:
//   - A document opened from Markdown is converted and its state stored before
//     anyone can edit it. Converted again later (after a restart, or on a
//     second instance), the same text becomes a structurally different Yjs
//     document, and a client still holding the first one would merge in a
//     second copy of everything.
//   - A store and a replace never interleave: both run under the document's
//     save lock.
//   - A replace lands in the live document only after the backend has
//     recorded it. It is computed on a copy, stored, and only then merged in —
//     as a merge, so whatever was typed meanwhile survives next to it.

import type { Document, Extension } from '@hocuspocus/server'
import type { IncomingMessage, ServerResponse } from 'node:http'

import { Redis } from '@hocuspocus/extension-redis'
import { Server } from '@hocuspocus/server'
import * as Y from 'yjs'

import { compareRoundTrip, exportMarkdown, readsAs, writeMarkdown } from '../src/lib/docSchema'

import { deriveKey, verifyBearer, verifyTicket } from './auth'
import { Backend, BackendError } from './backend'

export interface CollabConfig {
  port: number
  /** The backend's base URL, as this service reaches it. */
  backendUrl: string
  /** Shared with the backend's COLLAB_SECRET. */
  secret: string
  /** Set to share documents across instances. */
  redisUrl?: string
  /** Store this long after the last change… */
  debounceMs?: number
  /** …and at least this often while changes keep coming. */
  maxDebounceMs?: number
  /** How long to wait before retrying a store the backend did not take. */
  retryMs?: number
  quiet?: boolean
}

/** Who a connection is, from its ticket; never from what the client says. */
interface Context {
  handle: string
  agent: boolean
}

export function createCollabServer(config: CollabConfig): Server {
  const ticketKey = deriveKey(config.secret, 'ticket')
  const internalKey = deriveKey(config.secret, 'internal')
  const backend = new Backend(config.backendUrl, `Bearer ${internalKey}`)
  // Per document: whose changes the next store holds, and how many of them.
  const pending = new Map<string, Map<string, number>>()
  const retries = new Map<string, ReturnType<typeof setTimeout>>()

  function noteChange(name: string, handle: string) {
    const counts = pending.get(name) ?? new Map<string, number>()
    counts.set(handle, (counts.get(handle) ?? 0) + 1)
    pending.set(name, counts)
  }

  function takeActors(name: string): Map<string, number> {
    const counts = pending.get(name) ?? new Map<string, number>()
    pending.delete(name)
    return counts
  }

  function putBack(name: string, counts: Map<string, number>) {
    for (const [handle, n] of counts) {
      for (let i = 0; i < n; i++) noteChange(name, handle)
    }
  }

  // Runs under the document's save lock.
  async function storeLive(document: Document) {
    const name = document.name
    const counts = takeActors(name)
    const actors = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([handle]) => handle)
    try {
      await backend.store(name, {
        state: Y.encodeStateAsUpdate(document),
        content: exportMarkdown(document),
        actors,
      })
    } catch (error) {
      putBack(name, counts)
      // The document stays in memory, unsaved. Try again on our own rather than
      // waiting for the next keystroke, which may never come.
      if (!retries.has(name)) {
        retries.set(
          name,
          setTimeout(() => {
            retries.delete(name)
            void document.saveMutex.runExclusive(() => storeLive(document)).catch(() => {})
          }, config.retryMs ?? 5000)
        )
      }
      throw error
    }
  }

  const documents: Extension = {
    async onAuthenticate({ token, documentName, connectionConfig }) {
      const ticket = verifyTicket(token, ticketKey)
      if (ticket.doc !== documentName) throw new Error('the ticket opens another document')
      connectionConfig.readOnly = ticket.ro
      const context: Context = { handle: ticket.sub, agent: ticket.agent }
      return context
    },

    // The name and kind people see next to a caret come from the ticket, so
    // nobody can appear as somebody else.
    async beforeHandleAwareness({ states, context }) {
      const who = context as Context | undefined
      if (!who) return
      for (const state of states.values()) {
        state.user = { ...(state.user ?? {}), handle: who.handle, agent: who.agent }
      }
    },

    async onLoadDocument({ document, documentName }) {
      const loaded = await backend.load(documentName)
      if (loaded.state) {
        Y.applyUpdate(document, Buffer.from(loaded.state, 'base64'))
        return document
      }
      if (!loaded.content.trim()) return document
      writeMarkdown(document, loaded.content)
      const report = compareRoundTrip(loaded.content, exportMarkdown(document))
      if (!report.clean) {
        // The original stays in the version history; this says where to look.
        console.warn(`[collab] ${documentName}: converting changed the Markdown\n${report.diff}`)
      }
      await backend.store(documentName, { state: Y.encodeStateAsUpdate(document), content: null, actors: [] })
      return document
    },

    async onChange({ documentName, context }) {
      const who = context as Context | undefined
      if (who?.handle) noteChange(documentName, who.handle)
    },

    async onStoreDocument({ document }) {
      await storeLive(document)
    },

    async onRequest({ request, response, instance }) {
      const url = new URL(request.url ?? '/', 'http://collab')
      if (request.method === 'GET' && url.pathname === '/healthz') {
        respond(response, 200, { ok: true })
        throw null
      }
      const match = /^\/internal\/documents\/([^/]+)\/replace$/.exec(url.pathname)
      if (!match || request.method !== 'POST') return
      if (!verifyBearer(request.headers.authorization, internalKey)) {
        respond(response, 403, { message: 'not the backend' })
        throw null
      }
      const name = decodeURIComponent(match[1])
      const body = (await readJson(request)) as {
        content: string
        base: string | null
        actor: string
        operation?: Record<string, unknown> | null
      }
      const connection = await instance.openDirectConnection(name, { handle: body.actor, agent: true })
      try {
        const document = connection.document as Document
        const outcome = await document.saveMutex.runExclusive(async () => {
          if (!readsAs(document, body.base)) return { conflict: true as const }
          const copy = new Y.Doc()
          Y.applyUpdate(copy, Y.encodeStateAsUpdate(document))
          writeMarkdown(copy, body.content)
          const stored = await backend.store(name, {
            state: Y.encodeStateAsUpdate(copy),
            content: exportMarkdown(copy),
            actors: [body.actor],
            operation: body.operation ?? null,
          })
          Y.applyUpdate(document, Y.encodeStateAsUpdate(copy, Y.encodeStateVector(document)))
          return { conflict: false as const, stored }
        })
        if (outcome.conflict) {
          // The writer read an older document. Store what people typed since,
          // so that when it reads again it gets the document it is up against.
          const debounceId = `onStoreDocument-${name}`
          if (instance.debouncer.isDebounced(debounceId)) await instance.debouncer.executeNow(debounceId)
          await document.saveMutex.waitForUnlock()
          const { doc_version } = await backend.load(name)
          respond(response, 409, { doc_version })
        } else {
          respond(response, 200, { stored: outcome.stored })
        }
      } catch (error) {
        if (error instanceof BackendError && error.status === 409) {
          respond(response, 409, { error: 'operation', message: error.reason })
        } else {
          console.error(`[collab] replace ${name} failed`, error)
          respond(response, 502, { message: 'the document could not be stored' })
        }
      } finally {
        await connection.disconnect()
      }
      throw null
    },
  }

  const extensions: Extension[] = []
  if (config.redisUrl) {
    const url = new URL(config.redisUrl)
    extensions.push(
      new Redis({
        host: url.hostname,
        port: Number(url.port || 6379),
        options: {
          username: url.username || undefined,
          password: url.password ? decodeURIComponent(url.password) : undefined,
          db: Number(url.pathname.replace('/', '') || 0),
        },
        // A store waits on the backend; the default one-second lock would
        // expire mid-store and let a second instance store over it.
        lockTimeout: 30_000,
      })
    )
  }
  extensions.push(documents)

  return new Server({
    port: config.port,
    quiet: config.quiet ?? false,
    debounce: config.debounceMs ?? 3000,
    maxDebounce: config.maxDebounceMs ?? 30000,
    extensions,
  })
}

function respond(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(body))
}

function readJson(request: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    request.on('data', (chunk: Buffer) => chunks.push(chunk))
    request.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
      } catch (error) {
        reject(error)
      }
    })
    request.on('error', reject)
  })
}
