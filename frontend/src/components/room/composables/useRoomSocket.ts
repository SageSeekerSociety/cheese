/**
 * 房间这条 WebSocket 的**传输层**：连上、断了自动重连、心跳、换掉假活的那条、关掉。
 *
 * **它不认识任何一种帧的含义。** 帧怎么解读是房间的状态机（`handleFrame`），那件事
 * 要碰消息列表、待办、轮次、发件箱、错误横幅十几样东西，搬进来只会把同一堆东西
 * 换个地方放，外加一层间接。所以这里收到帧就原样交出去。
 *
 * 出口只有一个 `post`：外面拿不到那个 socket 对象，也就不会有第二处在它身上挂
 * 回调——「哪条 socket 是当前那条」的判断只存在于这个文件里。
 */

import type { Ref } from 'vue'
import type { WsClientMessage, WsServerFrame } from '../../../cx_types'

import { onScopeDispose, ref } from 'vue'
import { useEventListener } from '@vueuse/core'

import { chatWsUrl } from '../../../api'

export function useRoomSocket(options: {
  /** 此刻在哪个话题上；切走了就不该再为上一个重连。 */
  topicId: () => string | undefined
  /** 收到一帧（`pong` 已经在这里吃掉了）。 */
  onFrame: (frame: WsServerFrame) => void
  /** 刚连上：这是把断线期间攒下的东西放出去的时刻。 */
  onOpen: () => void
  /**
   * 这条链路没了（关掉，或者被判定假活换掉）。正在等回声的那几条消息失去了通道，
   * 该重新排队，而不是让它们的定时器判定「没送到」。
   */
  onDrop: () => void
  /** 重连：重新拉一遍历史再开一条新的——断线期间漏掉的消息要补回来。 */
  reconnect: (topicId: string) => void
  /** 房间那条错误横幅。连上要清掉它，断了要在上面写原因。 */
  errorMsg: Ref<string | null>
}) {
  const connected = ref(false)

  // Auto-reconnect (协作软件语义): a backend deploy/restart must be a blip, not a
  // frozen pane needing a manual refresh. An UNEXPECTED close schedules a
  // reconnect with backoff; every deliberate teardown funnels through
  // closeSocket(), which cancels it. The reconnect callback refetches history so
  // gaps from the outage are filled in.
  let socket: WebSocket | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelayMs = 1000

  function cancelRetry() {
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  // Every way the backend can refuse a socket AT CONNECT (app/api/routes/chat.py):
  // no token, a token it could not verify, and a verified token whose owner is not
  // on this topic's roster. The set is the point — `forbidden` was left out once
  // and behaved exactly like the bug this latch exists to fix, because a refusal
  // the client doesn't recognise falls through to the reconnect path below.
  const CONNECT_REFUSAL_CODES = new Set(['auth_required', 'auth_expired', 'forbidden'])

  /** 这个 error 帧说的是「连接被拒」而不是「出了点事」。 */
  function isConnectRefusal(code: string | undefined): boolean {
    return !!code && CONNECT_REFUSAL_CODES.has(code)
  }

  // A connect refusal is not an outage: the backend closes the socket after one
  // error frame, so retrying just reopens and gets refused again. And it does not
  // even back off — the HANDSHAKE succeeds, the refusal arrives as a frame, so
  // onopen has already cleared the banner and reset retryDelayMs to 1s before the
  // reason lands. Measured with `forbidden` unlatched: 9 connections in 8 seconds,
  // the green dot flickering and the reason blinking with it, forever. So we latch
  // it: stop retrying and keep the reason on screen until they act.
  const connectRefused = ref(false)

  function scheduleReconnect(topicId: string) {
    if (retryTimer || connectRefused.value) return
    const delay = retryDelayMs
    retryDelayMs = Math.min(retryDelayMs * 2, 15000)
    retryTimer = setTimeout(() => {
      retryTimer = null
      // Only if the user is still on this topic (switching cancels via closeSocket,
      // but double-check against races).
      if (options.topicId() === topicId) options.reconnect(topicId)
    }, delay)
  }

  function closeSocket() {
    cancelRetry()
    stopHeartbeat()
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      socket.close()
      socket = null
    }
    connected.value = false
  }

  // OPEN is only the browser's last observation: a socket whose path stopped
  // carrying frames stays OPEN until TCP gives up, which took 6.5 minutes once.
  // Whoever decides the link is gone (no echo for a sent message, no answer to a
  // ping) comes here: drop that socket without telling it, queue what it was
  // carrying, and let `reconnect` reconcile history and open a fresh one.
  function replaceStaleSocket() {
    const topicId = options.topicId()
    const stale = socket
    if (!topicId || !stale) return false
    options.onDrop()
    stopHeartbeat()
    socket = null
    stale.onopen = null
    stale.onmessage = null
    stale.onerror = null
    stale.onclose = null
    stale.close()
    connected.value = false
    options.reconnect(topicId)
    return true
  }

  // Liveness probe. A page that is only waiting for 芝士's reply sends nothing,
  // so without this a dead link is noticed only when the next message goes
  // unanswered. Any frame counts as an answer — the reply is traffic too.
  const HEARTBEAT_INTERVAL_MS = 15_000
  const HEARTBEAT_TIMEOUT_MS = 10_000
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let pongTimer: ReturnType<typeof setTimeout> | null = null

  function noteHeartbeatAnswer() {
    if (pongTimer) clearTimeout(pongTimer)
    pongTimer = null
  }

  function stopHeartbeat() {
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    heartbeatTimer = null
    noteHeartbeatAnswer()
  }

  function startHeartbeat(ws: WebSocket) {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (socket !== ws || ws.readyState !== WebSocket.OPEN || pongTimer) return
      const ping: WsClientMessage = { type: 'ping' }
      ws.send(JSON.stringify(ping))
      pongTimer = setTimeout(() => {
        pongTimer = null
        if (socket === ws) replaceStaleSocket()
      }, HEARTBEAT_TIMEOUT_MS)
    }, HEARTBEAT_INTERVAL_MS)
  }

  function openSocket(topicId: string) {
    closeSocket()
    const ws = new WebSocket(chatWsUrl(topicId))
    socket = ws

    ws.onopen = () => {
      connected.value = true
      retryDelayMs = 1000 // healthy again → next outage starts backoff fresh
      options.errorMsg.value = null
      startHeartbeat(ws)
      options.onOpen()
    }
    ws.onclose = () => {
      if (socket === ws) {
        connected.value = false
        stopHeartbeat()
        // Anything still waiting for an echo lost its channel — queue it again
        // rather than let its timer call it undelivered while we reconnect.
        options.onDrop()
        scheduleReconnect(topicId)
      }
    }
    ws.onerror = () => {
      // The close handler owns retry; the banner just explains the grey dot.
      if (!connectRefused.value) options.errorMsg.value = '连接断开，正在自动重连…'
    }
    ws.onmessage = (ev: MessageEvent) => {
      // Guard against frames from a stale socket after topic switch.
      if (socket !== ws) return
      let frame: WsServerFrame
      try {
        frame = JSON.parse(ev.data as string) as WsServerFrame
      } catch {
        return
      }
      noteHeartbeatAnswer()
      if (frame.type === 'pong') return
      options.onFrame(frame)
    }
  }

  // 有网就自动转出来 (owner spec): the offline→online transition is our cue to
  // reconnect NOW rather than wait out the backoff, and to refetch history so
  // messages that landed during the outage are pulled in — `reconnect` re-runs the
  // history fetch and reopens the socket, and the caller dedups the overlap. Guards:
  // a connect refusal is an auth problem, not an outage (leave it latched); a
  // still-healthy socket needs nothing; no active topic, nothing to do.
  function reconnectOnOnline() {
    const topicId = options.topicId()
    if (connectRefused.value || connected.value || !topicId) return
    cancelRetry()
    retryDelayMs = 1000 // recovered → next outage starts backoff fresh
    options.reconnect(topicId)
  }
  useEventListener(window, 'online', reconnectOnOnline)
  onScopeDispose(closeSocket)

  return {
    connected,
    /** 连接在开始阶段就被拒了（认证/权限），重连帮不上忙——由房间的状态机置位。 */
    connectRefused,
    isConnectRefusal,
    /** 安排一次带退避的重连。拉历史失败时也走这里——那同样是链路不好。 */
    retryLater: scheduleReconnect,
    /** 每次连接都从这里进。旧的那条会先被干净地关掉。 */
    open: openSocket,
    close: closeSocket,
    /** 发一帧。没连上返回 false，由调用方决定是排队还是报错。 */
    post(message: WsClientMessage): boolean {
      if (!socket || socket.readyState !== WebSocket.OPEN) return false
      socket.send(JSON.stringify(message))
      return true
    },
    /** 这条 socket 还 OPEN 着但不再送帧了——扔掉它，重新连。 */
    replaceStale: replaceStaleSocket,
  }
}
