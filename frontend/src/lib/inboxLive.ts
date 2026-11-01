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
/** 和芝士的这段对话说了新的话：一个回答答完了，或例行事务运行了。 */
export type AssistantListener = (conversation: string) => void

const NAME = 'cheese-inbox-live'
const FIRST_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

const listeners = new Set<InboxListener>()
const assistantListeners = new Set<AssistantListener>()

/** 收件箱有新的一行时叫 `listener`；返回的函数取消。 */
export function onInboxChange(listener: InboxListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function tell(): void {
  for (const listener of listeners) listener()
}

/** 和芝士的哪段对话说了新的话时叫 `listener`（别的标签页、别的设备上也算）；返回的函数取消。 */
export function onAssistantChange(listener: AssistantListener): () => void {
  assistantListeners.add(listener)
  return () => assistantListeners.delete(listener)
}

function tellAssistant(conversation: string): void {
  for (const listener of assistantListeners) listener(conversation)
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

/**
 * 连上一次，直到断开；收到第一帧算连上过。`changed` 在最新一行变了时叫。
 *
 * `seen.latest` 是上一段连接手上那个号，跨重连带着走：第一帧只说「现在到几号
 * 了」，第一次连上时它不算新的（这一页刚要读），但重连时它比上次说的号大，说明
 * 断开期间来过了——那一次也得叫，不然列表和角标要等到下一次有人来找。
 */
function connectOnce(
  token: string,
  stopped: AbortSignal,
  changed: () => void,
  seen: { latest: number | null },
  assistant: AssistantListener = () => {}
): Promise<boolean> {
  return new Promise((resolve) => {
    let heard = false
    const socket = new WebSocket(inboxUrl())
    const stop = () => socket.close()
    stopped.addEventListener('abort', stop)
    socket.onopen = () => socket.send(JSON.stringify({ token }))
    socket.onmessage = (event) => {
      let frame: { kind?: string; latest?: number | null; conversation?: string }
      try {
        frame = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (frame.kind === 'assistant' && typeof frame.conversation === 'string') {
        assistant(frame.conversation)
        return
      }
      if (frame.kind !== 'inbox') return
      const latest = typeof frame.latest === 'number' ? frame.latest : null
      const missed = latest !== null && seen.latest !== null && latest > seen.latest
      if (heard || missed) changed()
      if (latest !== null) seen.latest = latest
      heard = true
    }
    socket.onclose = () => {
      stopped.removeEventListener('abort', stop)
      resolve(heard)
    }
  })
}

/** 这一页持着连接：断了重连，直到 `stopped`。 */
async function holdConnection(
  issue: () => Promise<string>,
  stopped: AbortSignal,
  changed: () => void,
  assistant: AssistantListener
): Promise<void> {
  let attempt = 0
  // 同一段登录里跨重连记着：断开期间收件箱往前走了，重连的第一帧要叫一次。
  const seen: { latest: number | null } = { latest: null }
  while (!stopped.aborted) {
    let heard = false
    try {
      // 每次都要一张新的：上一张可能已经随登录一起作废（服务端用 4401 关掉的那种）。
      heard = await connectOnce(await issue(), stopped, changed, seen, assistant)
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
  if (channel)
    channel.onmessage = (event: MessageEvent) => {
      const conversation = (event.data as { assistant?: unknown } | null)?.assistant
      if (typeof conversation === 'string') tellAssistant(conversation)
      else tell()
    }
  const changed = () => {
    tell()
    channel?.postMessage('changed')
  }
  const assistant = (conversation: string) => {
    tellAssistant(conversation)
    channel?.postMessage({ assistant: conversation })
  }

  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  if (locks && channel) {
    locks
      .request(NAME, { signal: stop.signal }, () => holdConnection(issue, stop.signal, changed, assistant))
      .catch(() => {})
  } else {
    void holdConnection(issue, stop.signal, changed, assistant)
  }

  return () => {
    stop.abort()
    channel?.close()
  }
}
