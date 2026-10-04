// One document, live: the Yjs document an editor binds to, the connection that
// keeps it in step with everyone else's, and who else is in it.
//
// The document is edited in the collaboration service (frontend/collab). This
// opens it with a ticket from the backend — asked again on every reconnect,
// because a ticket only opens a connection for a couple of minutes — and closes
// it when the document changes or the page goes away. Changes typed while the
// connection is down stay in the local document and merge in when it comes back.
//
// The same connection carries what the backend says about the document that is
// not its text (stateless messages from the service): a stored version moved on
// (`stores` counts them), its comments changed, an agent is answering a thread
// (both passed on through lib/docCommentSignals).
//
// Nothing here saves anything: the service stores the document a few seconds
// after the typing stops.
//
// The editor gets the document only once the service has let this page in. A
// page built with another document schema is refused (lib/docSchema/version.ts):
// bound to the document, it would drop what it cannot parse and the drop would
// reach everyone. Refused that way, the connection is closed and the page says
// it needs a refresh.

import type { DocTicket } from '../api/docCollab'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { HocuspocusProvider, HocuspocusProviderWebsocket } from '@hocuspocus/provider'
import * as Y from 'yjs'

import { avatarColor } from '@/utils/avatar'
import { getAvatarUrl } from '@/utils/materials'

import { collabWsUrl, getDocTicket } from '../api/docCollab'
import { announceComments } from '../lib/docCommentSignals'
import { DOC_SCHEMA_MISMATCH } from '../lib/docSchema/version'
import { myAccount } from '../me'

/** Somebody with the document open, as the service vouches for them. */
export interface DocPeer {
  clientId: number
  /** From the ticket; the service overwrites whatever a client claims. */
  handle: string
  agent: boolean
  /** What their own client calls them. */
  name: string
  avatar: string
  color: string
}

export interface DocSession {
  document: string
  doc: Y.Doc
  provider: HocuspocusProvider
  /** The local person, as their caret is labelled for everyone else. */
  user: { name: string; color: string; avatar: string }
}

export type DocConnection = 'connecting' | 'connected' | 'offline'

function connectToService(ticket: () => Promise<DocTicket>, first: DocTicket) {
  const doc = new Y.Doc()
  const socket = new HocuspocusProviderWebsocket({ url: collabWsUrl() })
  let fresh: DocTicket | null = first
  const provider = new HocuspocusProvider({
    websocketProvider: socket,
    name: first.document,
    document: doc,
    // The first ticket was just fetched; every later connection asks again.
    token: async () => {
      const use = fresh ?? (await ticket())
      fresh = null
      return use.ticket
    },
  })
  provider.attach()
  return { doc, provider, destroy: () => (provider.destroy(), socket.destroy()) }
}

/** What the backend tells a document's open editors, as the service relays it. */
function heard(document: string, payload: string, stored: () => void) {
  let frame: Record<string, unknown>
  try {
    frame = JSON.parse(payload)
  } catch {
    return
  }
  if (frame.type === 'state' && frame.resource === 'doc') stored()
  else if (frame.type === 'state' && frame.resource === 'comments') announceComments(document, { kind: 'changed' })
  else if (frame.type === 'comment_activity' && typeof frame.thread === 'string') {
    const state = frame.state === 'working' ? 'working' : 'queued'
    const tool = typeof frame.tool === 'string' ? frame.tool : undefined
    announceComments(document, { kind: 'activity', thread: frame.thread, state, tool })
  }
}

export function useDocCollab(document: () => string | null) {
  const session = shallowRef<DocSession | null>(null)
  const connection = ref<DocConnection>('connecting')
  /** True once the document has arrived from the service at least once. */
  const synced = ref(false)
  /** The ticket said this person may only read. */
  const readOnly = ref(false)
  const peers = shallowRef<DocPeer[]>([])
  const error = ref<string | null>(null)
  /** The service speaks another document schema: this page has to be reloaded. */
  const outdated = ref(false)
  /** How many times a version of this document was stored while it was open. */
  const stores = ref(0)
  let close: (() => void) | null = null
  let generation = 0

  function teardown() {
    close?.()
    close = null
    session.value = null
    peers.value = []
    synced.value = false
    connection.value = 'connecting'
  }

  async function open(id: string) {
    const mine = ++generation
    teardown()
    error.value = null
    outdated.value = false
    let first: DocTicket
    try {
      first = await getDocTicket(id)
    } catch (cause) {
      if (mine === generation) {
        error.value = cause instanceof Error ? cause.message : String(cause)
        connection.value = 'offline'
      }
      return
    }
    if (mine !== generation) return
    readOnly.value = first.read_only
    const opened = connectToService(() => getDocTicket(id), first)
    const { doc, provider } = opened
    const account = myAccount()
    const user = {
      name: account?.nickname || account?.username || '',
      color: avatarColor(account?.username ?? ''),
      avatar: account ? getAvatarUrl(account.avatarId) : '',
    }
    provider.setAwarenessField('user', user)
    provider.on('status', ({ status }: { status: string }) => {
      connection.value = status === 'connected' ? 'connected' : status === 'connecting' ? 'connecting' : 'offline'
    })
    provider.on('synced', ({ state }: { state: boolean }) => {
      if (state) synced.value = true
    })
    provider.on('authenticated', () => {
      if (mine === generation && !session.value) session.value = { document: id, doc, provider, user }
    })
    provider.on('stateless', ({ payload }: { payload: string }) => {
      if (mine === generation) heard(id, payload, () => stores.value++)
    })
    provider.on('authenticationFailed', ({ reason }: { reason: string }) => {
      if (mine !== generation) return
      if (reason === DOC_SCHEMA_MISMATCH) {
        teardown()
        outdated.value = true
      }
      connection.value = 'offline'
    })
    const readPeers = () => {
      const states = provider.awareness?.getStates() ?? new Map()
      const out: DocPeer[] = []
      states.forEach((state, clientId) => {
        if (clientId === doc.clientID) return
        const u = (state as { user?: Record<string, unknown> }).user
        if (!u?.handle) return
        out.push({
          clientId,
          handle: String(u.handle),
          agent: !!u.agent,
          name: String(u.name || u.handle),
          avatar: String(u.avatar || ''),
          color: String(u.color || avatarColor(String(u.handle))),
        })
      })
      peers.value = out
    }
    provider.awareness?.on('change', readPeers)
    close = () => {
      provider.awareness?.off('change', readPeers)
      opened.destroy()
      doc.destroy()
    }
  }

  watch(
    document,
    (id) => {
      if (id) void open(id)
      else {
        generation++
        teardown()
      }
    },
    { immediate: true }
  )

  onBeforeUnmount(() => {
    generation++
    teardown()
  })

  return { session, connection, synced, readOnly, peers, error, outdated, stores }
}
