// 「这块板的学情分析变了」：服务端在结果变了的时候说一声（`WS
// /spaces/{id}/learning-analysis/live`，见 `backend/app/api/routes/spaces_learning_live.py`）。
//
// 帧里只有「这块板变了」；读哪几条判断仍由页面自己去读原来那两条 GET —— 与重新打开
// 页面读的是同一份接口，所以「只有本人」「不足 3 人，不分析」两条规矩照旧由服务端把关，
// 这里只负责知道「该重读了」。
//
// 断了就重连，等多久按「指数退避 + 全抖动」（与 `lib/inboxLive.ts` 同一个办法）：一次
// 部署会让所有页同时断开，不抖开就是同一秒一齐涌回来。网络恢复时立刻再试；回来之后先叫
// 一次 —— 断开期间漏掉的帧由这一次补上。
import { authToken, BASE } from '@/api/http'

/** 一块板变了（或者连接刚建立 / 重连上）。 */
export type LearningLiveListener = () => void

/** 一块板一条连接；同一个页面开两条引用时共用一条。 */
type Connection = {
  listeners: Set<LearningLiveListener>
  socket: WebSocket | null
  stopped: boolean
  attempt: number
  timer: number | null
}

const connections = new Map<number, Connection>()

const FIRST_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

function liveUrl(spaceId: number): string {
  const token = authToken()
  const query = token ? `?token=${encodeURIComponent(token)}` : ''
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}${BASE}/spaces/${spaceId}/learning-analysis/live${query}`
}

/** 第 `attempt` 次重连前等多久：0 到 min(上限, 起步 × 2^attempt) 之间随机。 */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  return Math.floor(random() * Math.min(MAX_DELAY_MS, FIRST_DELAY_MS * 2 ** attempt))
}

function tell(connection: Connection) {
  for (const listener of connection.listeners) listener()
}

function connect(spaceId: number, connection: Connection) {
  if (connection.stopped || typeof WebSocket === 'undefined') return
  let socket: WebSocket
  try {
    socket = new WebSocket(liveUrl(spaceId))
  } catch {
    schedule(spaceId, connection)
    return
  }
  connection.socket = socket
  socket.onmessage = (event) => {
    let frame: { type?: string }
    try {
      frame = JSON.parse(String(event.data))
    } catch {
      return
    }
    if (!frame || typeof frame !== 'object' || frame.type === 'beat') return
    connection.attempt = 0
    tell(connection)
  }
  socket.onclose = () => {
    connection.socket = null
    schedule(spaceId, connection)
  }
  socket.onerror = () => {
    // `onclose` follows; closing here makes a browser that only fires one of
    // the two reach the same place.
    try {
      socket.close()
    } catch {
      /* already closed */
    }
  }
}

function schedule(spaceId: number, connection: Connection) {
  if (connection.stopped || connection.timer !== null) return
  const wait = reconnectDelay(connection.attempt)
  connection.attempt += 1
  connection.timer = window.setTimeout(() => {
    connection.timer = null
    connect(spaceId, connection)
  }, wait)
}

/**
 * 订阅一块板：`listener` 在连接建立时叫一次，之后每次这块板变了再叫一次。返回的函数
 * 取消这一个订阅；最后一个订阅取消时连接关掉。
 */
export function onLearningLive(spaceId: number, listener: LearningLiveListener): () => void {
  let connection = connections.get(spaceId)
  if (connection === undefined) {
    connection = { listeners: new Set(), socket: null, stopped: false, attempt: 0, timer: null }
    connections.set(spaceId, connection)
    connect(spaceId, connection)
  }
  connection.listeners.add(listener)
  const joined = connection
  const onOnline = () => {
    if (joined.stopped || joined.socket !== null) return
    if (joined.timer !== null) {
      window.clearTimeout(joined.timer)
      joined.timer = null
    }
    joined.attempt = 0
    connect(spaceId, joined)
  }
  window.addEventListener('online', onOnline)
  return () => {
    window.removeEventListener('online', onOnline)
    joined.listeners.delete(listener)
    if (joined.listeners.size > 0) return
    joined.stopped = true
    connections.delete(spaceId)
    if (joined.timer !== null) window.clearTimeout(joined.timer)
    joined.timer = null
    joined.socket?.close()
    joined.socket = null
  }
}

/** Close every learning connection (tests that mount a page twice in one process;
 *  `setup-network.ts` calls it between tests like `resetRoomLink`). */
export function resetLearningLive(): void {
  for (const connection of connections.values()) {
    connection.stopped = true
    if (connection.timer !== null) window.clearTimeout(connection.timer)
    connection.timer = null
    connection.socket?.close()
    connection.socket = null
  }
  connections.clear()
}
