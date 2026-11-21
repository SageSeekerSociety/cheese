// The language servers of a task's checkout, for the 文件 panel's editor: one
// socket per task and server on the page (`taskLanguageWsUrl`), shared by every
// cell of the task that shows a file that server reads.
//
// A socket is opened only while some editor has such a file open and the task's
// machine is up (as `useTaskFiles` knows it): this never brings a machine up. The
// first TEXT frame says `ready` (with where the checkout sits on the machine) or
// `refused`; after `ready`, BINARY frames carry the server's stdio both ways,
// spoken by `lib/lsp/client` (loaded only then). Nothing here shows anything: with
// no server the editor is what it is without one.
//
// A refusal stands until something changes — the machine becoming ready again,
// the page coming back online. A socket that drops is tried again a few times,
// further apart each time, then left until one of those.
import type { LspClient } from '../lib/lsp/client'
import type { LanguageLink, LanguageServer } from '../lib/lsp/link'
import type { TaskMachineState } from './useTaskFiles'

import { computed, onBeforeUnmount, watch } from 'vue'

import { taskLanguageWsUrl } from '@/api'

/**
 * The server each Monaco language goes to, named by the `language` of the socket's URL. C and
 * C++ are one server (clangd, the machine's `runtime_language.SERVERS`), so a task with both
 * open runs one of it; each document still tells the server its own language.
 */
const SERVERS: Record<string, string> = { c: 'c', cpp: 'c' }
/** How a dropped socket is tried again (ms). */
export const RETRY_MS = [1000, 2000, 4000, 8000, 16000]
/** How long a server no editor needs is kept, so flicking through files does not restart it (ms). */
export const LINGER_MS = 15000

type Listener = (client: LspClient | null) => void

/** One server of one task on this page. */
interface Connection {
  key: string
  taskId: string
  language: string
  listeners: Set<Listener>
  /** The machine is up, as the task's cells last said. */
  machineReady: boolean
  socket: WebSocket | null
  client: LspClient | null
  /** Said no; stands until the machine becomes ready again or the page comes online. */
  refused: boolean
  /** Dropped too many times in a row; same as a refusal until then. */
  gaveUp: boolean
  retries: number
  timer: ReturnType<typeof setTimeout> | null
  linger: ReturnType<typeof setTimeout> | null
}

const connections = new Map<string, Connection>()

function connectionOf(taskId: string, language: string): Connection {
  const key = `${taskId}\n${language}`
  let conn = connections.get(key)
  if (!conn) {
    conn = {
      key,
      taskId,
      language,
      listeners: new Set(),
      machineReady: false,
      socket: null,
      client: null,
      refused: false,
      gaveUp: false,
      retries: 0,
      timer: null,
      linger: null,
    }
    connections.set(key, conn)
  }
  return conn
}

function tell(conn: Connection, client: LspClient | null) {
  for (const listener of [...conn.listeners]) listener(client)
}

/** Opens the socket when it is wanted and may be tried. */
function settle(conn: Connection) {
  if (conn.listeners.size > 0 && conn.linger) {
    clearTimeout(conn.linger)
    conn.linger = null
  }
  if (conn.listeners.size === 0) {
    if (conn.socket && !conn.linger) conn.linger = setTimeout(() => end(conn), LINGER_MS)
    if (!conn.socket) end(conn)
    return
  }
  if (!conn.machineReady || conn.socket || conn.timer || conn.refused || conn.gaveUp) return
  connect(conn)
}

/** Nothing needs this server any more: close it and forget it. */
function end(conn: Connection) {
  if (conn.linger) clearTimeout(conn.linger)
  if (conn.timer) clearTimeout(conn.timer)
  conn.linger = null
  conn.timer = null
  drop(conn)
  if (conn.listeners.size === 0) connections.delete(conn.key)
}

function drop(conn: Connection) {
  const socket = conn.socket
  const client = conn.client
  conn.socket = null
  conn.client = null
  if (socket) {
    socket.onmessage = null
    socket.onclose = null
    socket.close()
  }
  if (client) {
    client.dispose()
    tell(conn, null)
  }
}

function connect(conn: Connection) {
  const ws = new WebSocket(taskLanguageWsUrl(conn.taskId, conn.language))
  ws.binaryType = 'arraybuffer'
  conn.socket = ws
  const dataListeners = new Set<(bytes: Uint8Array) => void>()
  const closeListeners = new Set<() => void>()
  let ready = false

  ws.onmessage = (event: MessageEvent) => {
    if (conn.socket !== ws) return
    if (event.data instanceof ArrayBuffer) {
      if (!ready) return
      const bytes = new Uint8Array(event.data)
      for (const listener of [...dataListeners]) listener(bytes)
      return
    }
    if (ready) return
    let frame: { type?: string; root?: string }
    try {
      frame = JSON.parse(String(event.data))
    } catch {
      return
    }
    if (frame.type === 'refused') {
      conn.refused = true
      drop(conn)
      return
    }
    if (frame.type !== 'ready' || typeof frame.root !== 'string') return
    ready = true
    const root = frame.root
    void (async () => {
      try {
        const { LspClient } = await import('../lib/lsp/client')
        if (conn.socket !== ws) return
        const client = await LspClient.start(
          {
            send: (bytes) => {
              if (ws.readyState === WebSocket.OPEN) ws.send(bytes)
            },
            onData: (listener) => {
              dataListeners.add(listener)
              return () => dataListeners.delete(listener)
            },
            onClose: (listener) => {
              closeListeners.add(listener)
              return () => closeListeners.delete(listener)
            },
            // What follows is `onclose`'s: a drop, unless `drop` let go of the socket first.
            close: () => ws.close(),
          },
          root
        )
        if (conn.socket !== ws || client.isClosed) return client.dispose()
        conn.retries = 0
        conn.client = client
        tell(conn, client)
      } catch {
        // The server did not answer the handshake: closing makes it a drop.
        ws.close()
      }
    })()
  }

  ws.onclose = (event: CloseEvent) => {
    if (conn.socket !== ws) return
    for (const listener of [...closeListeners]) listener()
    const client = conn.client
    conn.socket = null
    conn.client = null
    if (client) tell(conn, null)
    // The door said no (authentication): trying again says no again.
    if (event.code === 1008 || event.code === 4401) {
      conn.refused = true
      return
    }
    dropped(conn)
  }
}

/** The socket went away without a refusal: tried again a few times, further apart. */
function dropped(conn: Connection) {
  conn.socket = null
  if (conn.listeners.size === 0) return end(conn)
  if (conn.retries >= RETRY_MS.length) {
    conn.gaveUp = true
    return
  }
  conn.timer = setTimeout(() => {
    conn.timer = null
    settle(conn)
  }, RETRY_MS[conn.retries++])
}

/** Something changed that may make a refused or abandoned server worth asking again. */
function freshStart(conn: Connection) {
  conn.refused = false
  conn.gaveUp = false
  conn.retries = 0
  settle(conn)
}

let onlineListening = false
function listenOnline() {
  if (onlineListening) return
  onlineListening = true
  window.addEventListener('online', () => {
    for (const conn of connections.values()) if (conn.refused || conn.gaveUp) freshStart(conn)
  })
}

export interface TaskLanguageOptions {
  taskId: () => string | null
  /** The task's machine, as the 文件 panel knows it. */
  machine: () => TaskMachineState
  /** Show a file of the checkout at a line, in this cell. */
  open: (path: string, line: number) => void
}

/**
 * The link a cell's editor takes to reach the task's language servers. Every cell of a task
 * makes its own link; the servers behind them are the same.
 */
export function useTaskLanguage(opts: TaskLanguageOptions) {
  listenOnline()
  /** The servers this cell's editors are using, and how to stop each use. */
  const uses = new Set<() => void>()

  const link = computed<LanguageLink | null>(() => {
    const taskId = opts.taskId()
    if (!taskId) return null
    return {
      scope: taskId,
      serverFor(languageId: string): LanguageServer | null {
        const language = SERVERS[languageId]
        if (!language) return null
        return {
          use(listener) {
            const conn = connectionOf(taskId, language)
            conn.machineReady = opts.machine() === 'ready'
            conn.listeners.add(listener)
            let stopped = false
            const stop = () => {
              if (stopped) return
              stopped = true
              uses.delete(stop)
              conn.listeners.delete(listener)
              settle(conn)
            }
            uses.add(stop)
            if (conn.client) listener(conn.client)
            settle(conn)
            return stop
          },
        }
      },
      open: (path, line) => opts.open(path, line),
    }
  })

  // The machine came up (again): every server of the task may be asked again.
  watch(
    () => [opts.taskId(), opts.machine()] as const,
    ([taskId, machine], previous) => {
      if (!taskId) return
      const ready = machine === 'ready'
      const becameReady = ready && (previous?.[1] !== 'ready' || previous?.[0] !== taskId)
      for (const conn of connections.values()) {
        if (conn.taskId !== taskId) continue
        conn.machineReady = ready
        if (becameReady) freshStart(conn)
      }
    }
  )

  onBeforeUnmount(() => {
    for (const stop of [...uses]) stop()
  })

  return { link }
}
