// The collaboration service: every document, live.
//
// People edit a document here together over a WebSocket (Hocuspocus, Yjs). The
// backend keeps the record: this service loads a document from it, and stores
// it back — the Yjs state and the Markdown exported from it — a few seconds
// after the typing stops. That store is the ONLY way a new version reaches the
// backend, so a write that does not come from an editor (芝士's
// `cheese_doc_set`, a restore) comes here too, through
// `/internal/documents/:name/replace` (the whole document) or
// `/internal/documents/:name/edit` (passages of it, directly or as suggestions;
// see ./edit.ts), and becomes a change to the live document. Written to the
// database directly it would be overwritten by the next store. What the
// backend has to tell a document's open editors that is not the text (its
// comments changed, an agent is answering a thread) comes through
// `/internal/documents/:name/tell`, as a stateless message.
//
// Three rules hold the record together:
//   - A document opened from Markdown is converted and its state stored before
//     anyone can edit it. Converted again later (after a restart, or on a
//     second instance), the same text becomes a structurally different Yjs
//     document, and a client still holding the first one would merge in a
//     second copy of everything.
//   - A store and a replace or an edit never interleave: all run under the
//     document's save lock.
//   - A replace or an edit lands in the live document only after the backend
//     has recorded it. It is computed on a copy, stored, and only then merged in —
//     as a merge, so whatever was typed meanwhile survives next to it.

import type { Document, Extension, Hocuspocus } from '@hocuspocus/server'
import type { IncomingMessage, ServerResponse } from 'node:http'

import { Redis } from '@hocuspocus/extension-redis'
import { Server } from '@hocuspocus/server'
import * as Y from 'yjs'

import {
  compareRoundTrip,
  DOC_SCHEMA_MISMATCH,
  DOC_SCHEMA_PARAM,
  DOC_SCHEMA_VERSION,
  exportMarkdown,
  liveNode,
  liveSuggestions,
  readsAs,
  writeMarkdown,
} from '../src/lib/docSchema'

import { deriveKey, verifyBearer, verifyTicket } from './auth'
import { Backend, BackendError } from './backend'
import { applyEdits, type Edit, type EditMode, writeNode } from './edit'
import { checkMarkdownWrite } from './writeCheck'

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
        suggestions: liveSuggestions(document),
      })
    } catch (error) {
      putBack(name, counts)
      // A refusal (the room is gone, the request is wrong) will not change by
      // asking again.
      const refused = error instanceof BackendError && error.status >= 400 && error.status < 500
      // Otherwise the document stays in memory, unsaved. Try again on our own
      // rather than waiting for the next keystroke, which may never come.
      if (!refused && !retries.has(name)) {
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
    async onAuthenticate({ token, documentName, connectionConfig, requestParameters }) {
      // A page built with another schema would drop what it cannot parse and
      // write the drop back as a deletion (see docSchema/version.ts). It is
      // refused before the document is loaded or synced; the reason tells a
      // current page to ask for a refresh.
      if (requestParameters.get(DOC_SCHEMA_PARAM) !== String(DOC_SCHEMA_VERSION)) {
        throw Object.assign(new Error('the page speaks another document schema'), { reason: DOC_SCHEMA_MISMATCH })
      }
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
      const exported = exportMarkdown(document)
      const report = compareRoundTrip(loaded.content, exported)
      if (!report.clean) {
        // The original stays in the version history; this says where to look.
        console.warn(`[collab] ${documentName}: converting changed the Markdown\n${report.diff}`)
      }
      // From here on the stored text is what the document exports, so whatever
      // reads it (芝士, search, comment anchors) reads the document people see.
      // A conversion that only respells the text is the platform's, and is not
      // news to anyone in the room.
      await backend.store(documentName, {
        state: Y.encodeStateAsUpdate(document),
        content: exported === loaded.content ? null : exported,
        actors: ['system'],
        converted: true,
        suggestions: liveSuggestions(document),
      })
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
      const match = /^\/internal\/documents\/([^/]+)\/(replace|edit|tell)$/.exec(url.pathname)
      if (!match || request.method !== 'POST') return
      if (!verifyBearer(request.headers.authorization, internalKey)) {
        respond(response, 403, { message: 'not the backend' })
        throw null
      }
      const name = decodeURIComponent(match[1])
      if (match[2] === 'tell') {
        // A frame for whoever has the document open (its comments changed, an
        // agent is answering a thread). Nobody has it open: nobody to tell,
        // and it is not loaded for this.
        const body = await readJson(request)
        instance.documents.get(name)?.broadcastStateless(JSON.stringify(body))
        respond(response, 200, {})
        throw null
      }
      if (match[2] === 'edit') {
        await edit(instance, name, (await readJson(request)) as EditBody, response)
        throw null
      }
      const body = (await readJson(request)) as {
        content: string
        base: string | null
        actor: string
        operation?: Record<string, unknown> | null
        /** A write in Markdown from outside an editor: refuse it if it would lose text. */
        check?: boolean
      }
      const problem = body.check ? checkMarkdownWrite(body.content) : null
      if (problem) {
        respond(response, 422, { error: 'content', message: problem.message, line: problem.line })
        throw null
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
            suggestions: liveSuggestions(copy),
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

  // Part of the document changed by a writer outside an editor (see ./edit.ts).
  // Like a replace, it is computed on a copy, stored, and only then merged in.
  async function edit(instance: Hocuspocus, name: string, body: EditBody, response: ServerResponse) {
    const connection = await instance.openDirectConnection(name, { handle: body.actor, agent: true })
    try {
      const document = connection.document as Document
      const outcome = await document.saveMutex.runExclusive(async () => {
        const result = applyEdits(liveNode(document), body.edits, body.mode, body.actor)
        if (!result.ok) return result
        const copy = new Y.Doc()
        Y.applyUpdate(copy, Y.encodeStateAsUpdate(document))
        writeNode(copy, result.doc)
        const stored = await backend.store(name, {
          state: Y.encodeStateAsUpdate(copy),
          content: exportMarkdown(copy),
          actors: [body.actor],
          suggestions: liveSuggestions(copy),
          requested_by: body.requested_by ?? null,
          edits: result.edits,
          suggested: body.mode === 'suggest',
          reason: body.reason ?? null,
        })
        Y.applyUpdate(document, Y.encodeStateAsUpdate(copy, Y.encodeStateVector(document)))
        return { ok: true as const, stored, edits: result.edits }
      })
      if (outcome.ok) respond(response, 200, { stored: outcome.stored, edits: outcome.edits })
      else respond(response, outcome.status, outcome.body)
    } catch (error) {
      console.error(`[collab] edit ${name} failed`, error)
      respond(response, 502, { message: 'the document could not be stored' })
    } finally {
      await connection.disconnect()
    }
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

interface EditBody {
  edits: Edit[]
  actor: string
  requested_by?: string | null
  mode: EditMode
  reason?: string | null
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
