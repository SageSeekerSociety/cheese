/**
 * 房间这条 WebSocket 的**传输层**：连上、断了自动重连、心跳、换掉假活的那条、关掉。
 *
 * **它不认识任何一种帧的含义。** 帧怎么解读是房间的状态机（`handleFrame`），那件事
 * 要碰消息列表、待办、轮次、发件箱、错误横幅十几样东西，搬进来只会把同一堆东西
 * 换个地方放，外加一层间接。所以这里收到帧就原样交出去。
 *
 * 它几乎只收不发：消息是 POST 出去的（`useOutbox`），这条 socket 只把房间里落下的
 * 东西推过来。发的只有心跳，和「我在打字」（`send`，见 `useRoomActivity`）。外面拿不到那个 socket 对象，也就不会有第二处在它身上挂回调
 * ——「哪条 socket 是当前那条」的判断只存在于这个文件里。
 */

import type { Ref } from 'vue'
import type { WsClientMessage, WsServerFrame } from '../../../cx_types'

import { onScopeDispose, ref } from 'vue'
import { useEventListener } from '@vueuse/core'

import { chatWsUrl } from '../../../api'
import { t } from '../../../i18n'

export function useRoomSocket(options: {
  /** 此刻在哪个话题上；切走了就不该再为上一个重连。 */
  topicId: () => string | undefined
  /** 连哪条 socket。不给就是这个话题的房间 socket；团队页给的是团队那条。 */
  url?: (topicId: string) => string
  /** 收到一帧（`pong` 已经在这里吃掉了）。 */
  onFrame: (frame: WsServerFrame) => void
  /** 刚连上：链路又通了，断线期间没送出去的消息可以再走一次。`reconnect` 为真表示这
   * 是同一间房断了又连回来的重连，而不是进这间房的第一次连接。 */
  onOpen: (reconnect: boolean) => void
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
  // 上一次成功连上的是哪一间房：把「重连」和「进这间房的第一次连接」分开。切到别的
  // 话题就把这里换成新的话题，所以回到旧话题仍算第一次连接（那一刻该重新载入）。
  let openedTopic: string | null = null

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

  // A drop the first reconnect heals is not news. A release reloads the app
  // router, and a reload cuts every room socket once its old workers retire —
  // several drops per release, each healed about a second later. Announced
  // on every one of them, a room that never stopped working read as 断联
  // several times an hour. So the banner waits until the link has been down
  // this long without coming back: past the 1 s retry, the history refetch and
  // the handshake over a slow path, short of a wait anyone would sit through
  // without wanting to be told.
  const OUTAGE_ANNOUNCE_MS = 8_000
  let downSince: number | null = null
  let announceTimer: ReturnType<typeof setTimeout> | null = null

  function announceOutage() {
    if (!connectRefused.value) options.errorMsg.value = t('work.room.socket.reconnecting')
  }

  function noteLinkDown() {
    if (downSince === null) {
      downSince = Date.now()
      announceTimer = setTimeout(() => {
        announceTimer = null
        if (!connected.value) announceOutage()
      }, OUTAGE_ANNOUNCE_MS)
    } else if (Date.now() - downSince >= OUTAGE_ANNOUNCE_MS) {
      // Already announced once and since wiped by a retry's history refetch
      // (loadTopic clears the banner): still down, so say so again at once.
      announceOutage()
    }
  }

  function noteLinkUp() {
    downSince = null
    if (announceTimer) clearTimeout(announceTimer)
    announceTimer = null
  }

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
  // A ping nobody answers decides the link is gone: drop that socket without
  // telling it, and let `reconnect` reconcile history and open a fresh one.
  function replaceStaleSocket() {
    const topicId = options.topicId()
    const stale = socket
    if (!topicId || !stale) return false
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
    const ws = new WebSocket((options.url ?? chatWsUrl)(topicId))
    socket = ws

    ws.onopen = () => {
      connected.value = true
      noteLinkUp()
      retryDelayMs = 1000 // healthy again → next outage starts backoff fresh
      options.errorMsg.value = null
      startHeartbeat(ws)
      const reconnect = openedTopic === topicId
      openedTopic = topicId
      options.onOpen(reconnect)
    }
    ws.onclose = () => {
      if (socket === ws) {
        connected.value = false
        stopHeartbeat()
        scheduleReconnect(topicId)
      }
    }
    ws.onerror = () => {
      // The close handler owns retry; the banner explains the grey dot once the
      // drop has lasted long enough to be one (see OUTAGE_ANNOUNCE_MS).
      noteLinkDown()
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

  /** 往当前这条 socket 说一句。没连上就不发：打字这种事，过了就过了。 */
  function send(message: WsClientMessage) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
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
  onScopeDispose(() => {
    closeSocket()
    noteLinkUp()
  })

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
    send,
  }
}
