<template>
  <div class="gotty-terminal d-flex flex-column h-100">
    <div class="gotty-toolbar d-flex align-center px-3 py-2">
      <v-chip size="small" variant="flat" :color="statusColor" class="mr-2 font-weight-medium">
        <v-icon icon="mdi-circle-medium" start size="18"></v-icon>
        {{ statusLabel }}
      </v-chip>

      <v-chip v-if="isController" size="small" variant="tonal" color="teal" class="mr-auto"> 你正在操作 </v-chip>
      <v-chip v-else size="small" variant="tonal" class="mr-auto"> 只读观察中（可自由滚动） </v-chip>

      <v-btn
        size="small"
        variant="flat"
        :color="isController ? undefined : 'amber-darken-2'"
        :loading="takeoverPending"
        :disabled="!connected"
        @click="toggleTakeover"
      >
        {{ isController ? '交还' : '接管' }}
      </v-btn>
    </div>

    <div ref="containerRef" class="gotty-viewport flex-1"></div>
  </div>
</template>

<script setup lang="ts">
import '@xterm/xterm/css/xterm.css'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { FitAddon } from '@xterm/addon-fit'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { Terminal } from '@xterm/xterm'

import { API_BASE_URL } from '@/network/utils'
import AccountService from '@/services/account'

type SessionPhase = 'running' | 'idle' | 'prompt' | 'exited' | 'connecting' | 'disconnected'

/**
 * Control-plane messages exchanged as WS TEXT frames with the backend, per
 * the cheese connector contract §5 (browser <-> backend/screen endpoint).
 */
type ServerControlMessage =
  | { t: 'status'; phase: SessionPhase; detail?: string }
  | { t: 'takeover'; on: boolean; granted?: boolean }

const props = defineProps<{
  /** Session identifier the workspace is attached to. */
  sessionId: string
  /**
   * Optional explicit auth token override. When omitted, the currently
   * logged-in user's access token is used; the backend still authorizes the
   * takeover/tool actions per-actor regardless of which token is presented.
   */
  token?: string
}>()

const containerRef = ref<HTMLDivElement>()
const isController = ref(false)
const takeoverPending = ref(false)
const connected = ref(false)
const phase = ref<SessionPhase>('connecting')

const statusLabel = computed(() => {
  switch (phase.value) {
    case 'running':
      return '运行中'
    case 'idle':
      return '空闲'
    case 'prompt':
      return '等待确认'
    case 'exited':
      return '已退出'
    case 'disconnected':
      return '已断开'
    default:
      return '连接中'
  }
})

const statusColor = computed(() => {
  switch (phase.value) {
    case 'running':
      return 'green'
    case 'prompt':
      return 'amber-darken-2'
    case 'exited':
    case 'disconnected':
      return 'grey'
    default:
      return 'grey-lighten-1'
  }
})

let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelayMs = 500
const MAX_RECONNECT_DELAY_MS = 10000
let pingTimer: ReturnType<typeof setInterval> | null = null
let destroyed = false

/** Encode raw bytes as base64, without going through UTF-16 string decoding pitfalls. */
function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }
  return btoa(binary)
}

/** Decode base64 back to raw bytes (inverse of bytesToBase64). */
function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

/** Build a webtty binary frame: opcode byte followed by an ascii payload. */
function encodeFrame(opcode: string, asciiPayload: string): ArrayBuffer {
  return new TextEncoder().encode(opcode + asciiPayload).buffer as ArrayBuffer
}

function sendFrame(opcode: string, asciiPayload: string) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(encodeFrame(opcode, asciiPayload))
  }
}

function sendControl(message: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message))
  }
}

function wsUrl(): string {
  // API_BASE_URL may be absolute (http://host:port) or a relative proxy prefix
  // (e.g. "/api-proxy" behind the vite/ingress dev proxy). Resolve against the
  // page origin so both forms work; a bare relative value would otherwise make
  // `new URL()` throw.
  const base = API_BASE_URL || window.location.origin
  const httpUrl = new URL(base, window.location.origin)
  const wsProtocol = httpUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = new URL(`/connector/session/${encodeURIComponent(props.sessionId)}/screen`, httpUrl)
  url.protocol = wsProtocol
  const token = props.token ?? AccountService.accessToken
  if (token) {
    url.searchParams.set('token', token)
  }
  return url.toString()
}

function handleControlMessage(raw: string) {
  let message: ServerControlMessage
  try {
    message = JSON.parse(raw)
  } catch {
    return
  }

  if (message.t === 'status') {
    phase.value = message.phase
  } else if (message.t === 'takeover') {
    // Any takeover ack clears the pending spinner. We are the controller only
    // when the session HAS a controller (on) AND the server granted it to us.
    // - grant to us:      on=true,  granted=true  -> controller
    // - grant to another: on=true,  granted=false -> read-only
    // - release:          on=false, granted=false -> read-only
    takeoverPending.value = false
    isController.value = message.on && message.granted !== false
  }
}

function handleBinaryFrame(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer)
  if (bytes.length === 0 || !term) return

  const opcode = String.fromCharCode(bytes[0])
  const rest = bytes.subarray(1)

  switch (opcode) {
    case '1': {
      // Output: base64(raw bytes). Hand xterm the raw bytes directly so its
      // own UTF-8 stream decoder handles multi-byte sequences split across
      // frames correctly (never manually decode to a JS string here).
      const asciiPayload = new TextDecoder('ascii').decode(rest)
      term.write(base64ToBytes(asciiPayload))
      break
    }
    case '2':
      // Pong: no payload, nothing to do.
      break
    case '3': {
      // SetWindowTitle: raw (non-base64) utf-8 bytes.
      const title = new TextDecoder('utf-8').decode(rest)
      document.title = title
      break
    }
    case '4':
    case '5':
    case '6':
      // SetPreferences / SetReconnect / SetBufferSize: informational only,
      // cheesed/backend defaults are already sane for our fixed session.
      break
    default:
      break
  }
}

function connect() {
  if (destroyed) return
  connected.value = false
  phase.value = 'connecting'

  const socket = new WebSocket(wsUrl())
  socket.binaryType = 'arraybuffer'
  ws = socket

  socket.onopen = () => {
    connected.value = true
    reconnectDelayMs = 500
    // The session is live once the socket opens. Fine-grained phase
    // (idle/prompt) is reported by the backend via {"t":"status"} — the thin
    // client no longer detects it — so default to "running" here and let a
    // real status message refine it if/when one arrives.
    if (phase.value === 'connecting') phase.value = 'running'
    fitAndNotify()
    pingTimer = setInterval(() => sendFrame('2', ''), 15000)
  }

  socket.onmessage = (event) => {
    if (typeof event.data === 'string') {
      handleControlMessage(event.data)
    } else {
      handleBinaryFrame(event.data as ArrayBuffer)
    }
  }

  socket.onclose = () => {
    connected.value = false
    phase.value = 'disconnected'
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
    scheduleReconnect()
  }

  socket.onerror = () => {
    socket.close()
  }
}

function scheduleReconnect() {
  if (destroyed || reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, reconnectDelayMs)
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS)
}

function toggleTakeover() {
  takeoverPending.value = true
  sendControl({ t: 'takeover', on: !isController.value })
}

function fitAndNotify() {
  if (!fitAddon || !term) return
  try {
    fitAddon.fit()
  } catch {
    return
  }
  sendFrame('3', JSON.stringify({ columns: term.cols, rows: term.rows }))
}

onMounted(async () => {
  const container = containerRef.value
  if (!container) return

  // Load the terminal webfonts BEFORE creating/opening xterm. xterm measures
  // its character cell size exactly once at open(); if that happens against a
  // fallback font (because our webfont hasn't downloaded yet), every real glyph
  // later overflows its too-narrow cell and the grid garbles -- spaces collapse
  // and CJK/wide glyphs overlap. Awaiting here makes the first measure correct.
  try {
    await Promise.all([
      document.fonts.load('13px "JetBrainsMono Nerd Font"'),
      document.fonts.load('700 13px "JetBrainsMono Nerd Font"'),
      document.fonts.load('13px "Cascadia Code"'),
      document.fonts.load('700 13px "Cascadia Code"'),
    ])
  } catch {
    /* best-effort: fall through and render with whatever is available */
  }
  if (destroyed) return

  const instance = new Terminal({
    scrollback: 10000,
    convertEol: true,
    cursorBlink: true,
    // Match a local Claude Code terminal: JetBrainsMono Nerd Font is embedded
    // as the primary; Cascadia Code (embedded) backfills the Braille spinners
    // and box-drawing arcs it lacks; OS symbol/emoji fonts cover the rest.
    fontFamily:
      '"JetBrainsMono Nerd Font", "Cascadia Code", "Noto Sans Symbols 2", "Segoe UI Symbol", "Apple Symbols", "Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", monospace',
    fontSize: 13,
    theme: {
      background: '#1F1B13',
      foreground: '#FAF7F0',
      cursor: '#F0A815',
      selectionBackground: '#0E746855',
    },
  })
  const fit = new FitAddon()
  // Unicode 11 width tables so wide glyphs (emoji, some symbols) occupy the
  // correct number of cells and stay column-aligned. Optional: never let an
  // addon version mismatch break the terminal itself.
  try {
    const unicode11 = new Unicode11Addon()
    instance.loadAddon(unicode11)
    instance.unicode.activeVersion = '11'
  } catch {
    /* unicode11 addon unavailable/incompatible -- fall back to default widths */
  }
  instance.loadAddon(fit)
  instance.open(container)
  fit.fit()
  // Webfonts load asynchronously; re-fit once they are ready so xterm measures
  // its cell size against the real font rather than a fallback, avoiding a
  // misaligned first paint.
  document.fonts.ready.then(() => {
    try {
      fit.fit()
    } catch {
      /* terminal may already be torn down */
    }
  })

  term = instance
  fitAddon = fit

  // Local scroll always works via xterm's own scrollback buffer, regardless
  // of controller state — it never touches the network.
  instance.onData((data) => {
    if (!isController.value) return
    sendFrame('1', bytesToBase64(new TextEncoder().encode(data)))
  })

  instance.onResize(() => {
    sendFrame('3', JSON.stringify({ columns: instance.cols, rows: instance.rows }))
  })

  resizeObserver = new ResizeObserver(() => fitAndNotify())
  resizeObserver.observe(container)

  connect()
})

onBeforeUnmount(() => {
  destroyed = true
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (pingTimer) clearInterval(pingTimer)
  resizeObserver?.disconnect()
  ws?.close()
  term?.dispose()
})
</script>

<style scoped lang="scss">
.gotty-terminal {
  min-height: 0;
  background: #1f1b13;
}

.gotty-toolbar {
  background: #f1ecdf;
  border-bottom: 1px solid #e5ddcb;
  flex: none;
}

.gotty-viewport {
  min-height: 0;
  padding: 8px;

  :deep(.xterm) {
    height: 100%;
  }
}
</style>

<style>
/* Embedded terminal fonts (served from /public/fonts). JetBrainsMono Nerd Font
   is the primary to match a local Claude Code terminal; Cascadia Code backfills
   Braille + box-drawing arcs. Global (unscoped) so @font-face is registered
   document-wide for xterm's canvas renderer. */
@font-face {
  font-family: 'JetBrainsMono Nerd Font';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('/fonts/JetBrainsMonoNF-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'JetBrainsMono Nerd Font';
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url('/fonts/JetBrainsMonoNF-Bold.woff2') format('woff2');
}
@font-face {
  font-family: 'Cascadia Code';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('/fonts/CascadiaCode-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'Cascadia Code';
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url('/fonts/CascadiaCode-Bold.woff2') format('woff2');
}
</style>
