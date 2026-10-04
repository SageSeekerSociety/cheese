// A stand-in for useDocCollab in component tests: each room has a "server"
// document, and every panel that opens the room gets its own client document
// kept in step with it — what the collaboration service does, minus the socket.
// `remoteEdit` is somebody else typing.

import type { DocConnection, DocPeer, DocSession } from '../composables/useDocCollab'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import * as Y from 'yjs'

import { localDocSession } from '../lib/docLocalSession'
import { writeMarkdown } from '../lib/docSchema'

interface Room {
  server: Y.Doc
  readOnly: boolean
  error?: string
  /** The service speaks another document schema and refuses this page. */
  outdated?: boolean
  /** Not arrived yet: the panel waits until `arrive(id)`. */
  pending?: boolean
  waiting: (() => void)[]
}

const rooms = new Map<string, Room>()

export function seedRoom(
  id: string,
  markdown: string,
  opts: { readOnly?: boolean; error?: string; pending?: boolean; outdated?: boolean } = {}
) {
  const server = new Y.Doc()
  if (markdown) writeMarkdown(server, markdown)
  rooms.set(id, {
    server,
    readOnly: !!opts.readOnly,
    error: opts.error,
    outdated: opts.outdated,
    pending: opts.pending,
    waiting: [],
  })
}

/** The document of a pending room arrives. */
export function arrive(id: string) {
  const entry = rooms.get(id)!
  entry.pending = false
  entry.waiting.splice(0).forEach((go) => go())
}

export function resetRooms() {
  rooms.clear()
}

/** Somebody else rewrites the document to read `markdown`. */
export function remoteEdit(id: string, markdown: string) {
  writeMarkdown(rooms.get(id)!.server, markdown)
}

export function serverDoc(id: string): Y.Doc {
  return rooms.get(id)!.server
}

export function useFakeDocCollab(room: () => string | null) {
  const session = shallowRef<DocSession | null>(null)
  const connection = ref<DocConnection>('connecting')
  const synced = ref(false)
  const readOnly = ref(false)
  const peers = shallowRef<DocPeer[]>([])
  const error = ref<string | null>(null)
  const outdated = ref(false)
  let close: (() => void) | null = null

  function open(id: string | null) {
    close?.()
    close = null
    session.value = null
    synced.value = false
    error.value = null
    outdated.value = false
    if (!id) return
    // A room nobody seeded has an empty document, as a new room does.
    if (!rooms.has(id)) seedRoom(id, '')
    const entry = rooms.get(id)!
    if (entry.error) {
      error.value = entry.error
      connection.value = 'offline'
      return
    }
    if (entry.outdated) {
      outdated.value = true
      connection.value = 'offline'
      return
    }
    if (entry.pending) {
      entry.waiting.push(() => {
        if (room() === id) open(id)
      })
      return
    }
    readOnly.value = entry.readOnly
    const client = new Y.Doc()
    Y.applyUpdate(client, Y.encodeStateAsUpdate(entry.server))
    const down = (update: Uint8Array, origin: unknown) => {
      if (origin !== client) Y.applyUpdate(client, update, entry.server)
    }
    const up = (update: Uint8Array, origin: unknown) => {
      if (origin !== entry.server && !entry.readOnly) Y.applyUpdate(entry.server, update, client)
    }
    entry.server.on('update', down)
    client.on('update', up)
    close = () => {
      entry.server.off('update', down)
      client.off('update', up)
    }
    session.value = localDocSession('', id, client)
    connection.value = 'connected'
    synced.value = true
  }

  watch(room, open, { immediate: true })
  onBeforeUnmount(() => close?.())
  return { session, connection, synced, readOnly, peers, error, outdated, stores: ref(0) }
}
