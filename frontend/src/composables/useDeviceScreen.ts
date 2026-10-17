// 一台自有设备屏幕的**现场** socket：连上去、断了怎么办、回来的帧往哪儿送。
//
// 拆自 `components/DeviceLiveViewer.vue`：端点在 API 层（`screenWsUrl` 里带着会话令牌
// 和这个部署的 WS 前缀），所以连的是这一层，组件只管把字节喂给它的 xterm——组件不直接
// 碰 API 层（`.claude/rules/architecture.md` 的组件边界）。
//
// 后端定的那条线照旧：`/connector/session/{sid}/screen` 上的 TEXT 帧是控制（尺寸），
// BINARY 帧是屏幕数据；发出去的也一样，TEXT 是 `resize`，BINARY 是按键，所以控制永远
// 不会被当成输入。
import type { MaybeRefOrGetter } from 'vue'

import { onBeforeUnmount, ref, toValue, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { screenWsUrl } from '@/api'

export interface DeviceScreenHooks {
  /** 屏幕回来的东西：BINARY 是屏幕数据（字节），TEXT 是控制帧（原样的字符串）。 */
  onData: (chunk: Uint8Array | string) => void
  /** 连上了。**第一次尺寸要在这之后发**：后端据此把这一台会话挂上。 */
  onAttached?: () => void
  /** 换到另一台屏幕了：调用方手上那份缓冲已经不是这一台的。 */
  onSwitched?: () => void
}

export type DeviceScreenStatus = 'connecting' | 'open' | 'closed'

/**
 * @param sid 设备会话。换一台就重连，并把缓冲清掉。
 */
export function useDeviceScreen(sid: MaybeRefOrGetter<string>, hooks: DeviceScreenHooks) {
  const status = ref<DeviceScreenStatus>('connecting')
  let socket: WebSocket | null = null

  function isOpen(): boolean {
    return socket !== null && socket.readyState === WebSocket.OPEN
  }

  /** 发一帧。没连上就丢掉——不发没有去处的东西。 */
  function send(data: string | Uint8Array): void {
    if (isOpen()) socket?.send(data)
  }

  function teardown(): void {
    if (socket) {
      // 先把回调摘掉：`close()` 会让它回调回来，而那时候我们要的是「已经不管它了」。
      socket.onclose = null
      socket.onerror = null
      socket.close()
      socket = null
    }
  }

  /** 连到这一台屏幕。已有的连接先断掉；每次连都重算地址（令牌可能换了）。 */
  function connect(target: string): void {
    teardown()
    status.value = 'connecting'
    const ws = new WebSocket(screenWsUrl(target))
    ws.binaryType = 'arraybuffer'
    socket = ws
    ws.onopen = () => {
      status.value = 'open'
      hooks.onAttached?.() // first sized frame attaches this viewer on the backend
    }
    ws.onmessage = (ev: MessageEvent) => {
      if (ev.data instanceof ArrayBuffer) hooks.onData(new Uint8Array(ev.data))
      else if (typeof ev.data === 'string') hooks.onData(ev.data)
    }
    ws.onclose = () => {
      if (socket === ws) status.value = 'closed'
    }
    ws.onerror = () => {
      if (socket === ws) status.value = 'closed'
    }
  }

  // Switching to a different screen reconnects + clears the buffer.
  watch(
    () => toValue(sid),
    (target) => {
      hooks.onSwitched?.()
      connect(target)
    }
  )

  // 有网就自动转出来 (owner spec): if the 现场 socket dropped (a network blip closes
  // it and this read-only viewer has no retry loop by design — offline it simply
  // shows 已断开, and spinning is acceptable), reconnect the moment the network is
  // back. Driving recovery off the online edge means no busy loop can hammer the
  // hub while offline.
  useEventListener(window, 'online', () => {
    if (status.value === 'closed') connect(toValue(sid))
  })

  onBeforeUnmount(teardown)

  return { status, connect, send, isOpen }
}
