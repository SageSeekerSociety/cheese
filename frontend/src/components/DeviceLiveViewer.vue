<script setup lang="ts">
// 现场 (施工现场) for a self-hosted device agent (P3 Phase B): an xterm that
// renders a device screen's real terminal, byte-for-byte. Bytes arrive over
// `/connector/session/{sid}/screen` (the hub fans out raw `screen.data`); TEXT
// frames back are `resize` control, BINARY frames back are keystrokes — the
// split the backend defines, so control can never be mistaken for typing.
// Input rides the SAME authorization as watching (connector.py: whoever may
// view a screen may type into it — the trust boundary the agent already runs
// inside), so the component defaults to interactive; pass `readonly` where a
// surface wants perception without control.
import '@xterm/xterm/css/xterm.css'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'

import { screenWsUrl } from '../api'

const props = defineProps<{ sid: string; readonly?: boolean }>()

const host = ref<HTMLDivElement | null>(null)
const status = ref<'connecting' | 'open' | 'closed'>('connecting')
let term: Terminal | null = null
let fit: FitAddon | null = null
let socket: WebSocket | null = null
let ro: ResizeObserver | null = null

function sendResize(): void {
  if (!term || !socket || socket.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
}

function fitAndResize(): void {
  try {
    fit?.fit()
  } catch {
    // fit throws if the element has no layout yet — ignore, a later tick retries.
  }
  sendResize()
}

function connect(sid: string): void {
  teardownSocket()
  status.value = 'connecting'
  const ws = new WebSocket(screenWsUrl(sid))
  ws.binaryType = 'arraybuffer'
  socket = ws
  ws.onopen = () => {
    status.value = 'open'
    fitAndResize() // first sized frame attaches this viewer on the backend
  }
  ws.onmessage = (ev: MessageEvent) => {
    if (ev.data instanceof ArrayBuffer) {
      term?.write(new Uint8Array(ev.data))
    } else if (typeof ev.data === 'string') {
      term?.write(ev.data)
    }
  }
  ws.onclose = () => {
    if (socket === ws) status.value = 'closed'
  }
  ws.onerror = () => {
    if (socket === ws) status.value = 'closed'
  }
}

function teardownSocket(): void {
  if (socket) {
    socket.onclose = null
    socket.onerror = null
    socket.close()
    socket = null
  }
}

onMounted(() => {
  if (!host.value) return
  term = new Terminal({
    convertEol: false,
    disableStdin: props.readonly === true, // perception-only where asked
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    theme: { background: '#1e1e1e' },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(host.value)
  if (props.readonly !== true) {
    // Keystrokes go out as BINARY — the backend forwards them to the pane's
    // pty only after the viewer attached (sized), so nothing can type into a
    // screen that was never subscribed.
    const enc = new TextEncoder()
    term.onData((d) => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(enc.encode(d))
    })
  }
  fitAndResize()
  ro = new ResizeObserver(() => fitAndResize())
  ro.observe(host.value)
  connect(props.sid)
})

// Switching to a different screen reconnects + clears the buffer.
watch(
  () => props.sid,
  (sid) => {
    term?.reset()
    connect(sid)
  }
)

// 有网就自动转出来 (owner spec): if the 现场 socket dropped (a network blip closes
// it and this read-only viewer has no retry loop by design — offline it simply
// shows 已断开, and spinning is acceptable), reconnect the moment the network is
// back. Driving recovery off the online edge means no busy loop can hammer the
// hub while offline.
useEventListener(window, 'online', () => {
  if (status.value === 'closed') connect(props.sid)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  teardownSocket()
  term?.dispose()
  term = null
  fit = null
})
</script>

<template>
  <div class="device-live">
    <div v-if="status !== 'open'" class="device-live__status">
      {{ status === 'connecting' ? '连接现场中…' : '现场已断开' }}
    </div>
    <div ref="host" class="device-live__term"></div>
  </div>
</template>

<style scoped>
.device-live {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 240px;
  /* Theme-invariant on purpose: this is the ground of an xterm.js terminal,
     which paints its own dark scheme in both themes. A --surface here would
     flash a white slab under a black terminal. */
  background: #1e1e1e;
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.device-live__term {
  width: 100%;
  height: 100%;
  padding: 6px;
  box-sizing: border-box;
}
.device-live__status {
  position: absolute;
  top: 8px;
  right: 12px;
  z-index: 1;
  font-size: 12px;
  /* Sits ON the invariant terminal ground above, so it is light-on-dark in both
     themes — tokens here would make it dark-on-dark in the light theme. */
  color: #9aa0a6;
  background: rgba(0, 0, 0, 0.5);
  padding: 2px 8px;
  border-radius: var(--radius-lg);
}
</style>
