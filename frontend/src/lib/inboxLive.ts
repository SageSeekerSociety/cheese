// 收件箱有新的一行时，服务端当场告诉这个浏览器（`WS /notifications/inbox`，见
// `backend/app/api/routes/notifications_live.py`）。帧里只有「最新一行是几号」，
// 读哪几行、怎么显示由听的人自己去问——和重新打开页面读的是同一份接口。
//
// 一个浏览器只开一条：几个标签页用 Web Locks 选一个去连，拿到的消息经
// BroadcastChannel 转给其余的。持锁的那页关掉，锁放开，等着的下一页接上。浏览器
// 不给这两样时退回每页各连一条。
//
// 断了就重连，等多久按「指数退避 + 全抖动」：一次部署会让所有页同时断开，不抖开
// 就是同一秒一齐涌回来。网络恢复（`online`）时不等，立刻再试。

import { BASE } from '@/api/http'

/** 收件箱最新一行的 id 变了。 */
export type InboxListener = () => void

const NAME = 'cheese-inbox-live'
const FIRST_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

const listeners = new Set<InboxListener>()

/** 收件箱有新的一行时叫 `listener`；返回的函数取消。 */
export function onInboxChange(listener: InboxListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function tell(): void {
  for (const listener of listeners) listener()
}

/** 第 `attempt` 次重连前等多久：0 到 min(上限, 起步 × 2^attempt) 之间随机。 */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  return Math.floor(random() * Math.min(MAX_DELAY_MS, FIRST_DELAY_MS * 2 ** attempt))
}

function inboxUrl(): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}${BASE}/notifications/inbox`
}

/** 等 `ms` 毫秒；网络恢复或被停掉时提前回来。 */
function pause(ms: number, stopped: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      window.clearTimeout(timer)
      window.removeEventListener('online', done)
      stopped.removeEventListener('abort', done)
      resolve()
    }
    const timer = window.setTimeout(done, ms)
    window.addEventListener('online', done)
    stopped.addEventListener('abort', done)
  })
}

/** 连上一次，直到断开；收到第一帧算连上过。`changed` 在最新一行变了时叫。 */
function connectOnce(token: string, stopped: AbortSignal, changed: () => void): Promise<boolean> {
  return new Promise((resolve) => {
    let heard = false
    const socket = new WebSocket(inboxUrl())
    const stop = () => socket.close()
    stopped.addEventListener('abort', stop)
    socket.onopen = () => socket.send(JSON.stringify({ token }))
    socket.onmessage = (event) => {
      let frame: { kind?: string; latest?: number | null }
      try {
        frame = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (frame.kind !== 'inbox') return
      // 第一帧只说「现在到几号了」：这一页刚读过，或者断开期间漏掉的由页面回到前台
      // 那一次补上（`NotificationFeed.vue`）。之后每一帧都是新的一行。
      if (heard) changed()
      heard = true
    }
    socket.onclose = () => {
      stopped.removeEventListener('abort', stop)
      resolve(heard)
    }
  })
}

/** 这一页持着连接：断了重连，直到 `stopped`。 */
async function holdConnection(issue: () => Promise<string>, stopped: AbortSignal, changed: () => void): Promise<void> {
  let attempt = 0
  while (!stopped.aborted) {
    let heard = false
    try {
      // 每次都要一张新的：上一张可能已经随登录一起作废（服务端用 4401 关掉的那种）。
      heard = await connectOnce(await issue(), stopped, changed)
    } catch {
      // 凭证没要到（网络断了、登录过期）：和连不上一样，等一会儿再试。
    }
    if (stopped.aborted) return
    attempt = heard ? 0 : attempt + 1
    await pause(reconnectDelay(attempt), stopped)
  }
}

/**
 * 登录之后开始听；返回的函数在退出登录时调用。`issue` 向服务端要一张只能开这条
 * 连接的凭证（和桌面 app 用的是同一种）。
 */
export function startInboxLive(issue: () => Promise<string>): () => void {
  const stop = new AbortController()
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(NAME) : null
  if (channel) channel.onmessage = () => tell()
  const changed = () => {
    tell()
    channel?.postMessage('changed')
  }

  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  if (locks && channel) {
    locks.request(NAME, { signal: stop.signal }, () => holdConnection(issue, stop.signal, changed)).catch(() => {})
  } else {
    void holdConnection(issue, stop.signal, changed)
  }

  return () => {
    stop.abort()
    channel?.close()
  }
}
