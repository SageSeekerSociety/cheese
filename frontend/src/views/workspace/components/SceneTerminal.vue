<!-- 现场终端：直接操作一个 agent 的 Claude Code。说的是后端看客通道的原始协议
     （{type:resize}/{type:control} 加原始字节，与 misc/web-claude 一致），外观也照搬
     web-claude——纯黑底、JetBrainsMono Nerd Font、字号 14、行高 1.1。工具条：只读/滚动/
     交互三态、A−/A+ 字号、compact。发消息不在这里、走群聊。 -->
<template>
  <div class="st-wrap">
    <div class="st-bar">
      <button
        v-for="m in modes"
        :key="m.value"
        class="st-mode"
        :class="{ on: viewMode === m.value }"
        @click="setMode(m.value)"
      >
        {{ m.label }}
      </button>
      <span class="st-spacer" />
      <button class="st-mode" title="run Claude's /compact" @click="compact">🗜 compact</button>
      <button class="st-mode" title="字号减小" @click="setFont(fontSize - 1)">A−</button>
      <button class="st-mode" title="字号增大" @click="setFont(fontSize + 1)">A+</button>
    </div>
    <div ref="termEl" class="st-term" />
  </div>
</template>

<script setup lang="ts">
import '@xterm/xterm/css/xterm.css'

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'

import { authFetch } from '@/network/api/connectorFetch'
import { CONNECTOR_WS_BASE } from '@/network/utils'
import AccountService from '@/services/account'

const props = defineProps<{ sessionId: string; deviceId: string }>()

const termEl = ref<HTMLElement | null>(null)
const fontSize = ref(parseInt(localStorage.getItem('cheeseFont') || '14', 10))
type Mode = 'readonly' | 'scroll' | 'full'
const viewMode = ref<Mode>('readonly')
const modes: { value: Mode; label: string }[] = [
  { value: 'readonly', label: '只读' },
  { value: 'scroll', label: '滚动' },
  { value: 'full', label: '交互' },
]

let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let ro: ResizeObserver | null = null
let sizeTimer = 0
const enc = new TextEncoder()

function controlLevel(): string {
  return viewMode.value === 'full' ? 'full' : viewMode.value === 'scroll' ? 'scroll' : 'passive'
}
function sendControl(): void {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'control', level: controlLevel() }))
}
function sendSize(force: boolean): void {
  try {
    fit?.fit()
  } catch {
    /* not laid out yet */
  }
  if (!force && viewMode.value === 'readonly') return
  if (ws && ws.readyState === 1 && term) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
}
function setMode(m: Mode): void {
  viewMode.value = m
  sendSize(false)
  sendControl()
}
function setFont(n: number): void {
  const size = Math.max(8, Math.min(30, n))
  fontSize.value = size
  localStorage.setItem('cheeseFont', String(size))
  if (term) term.options.fontSize = size
  sendSize(false)
}
async function compact(): Promise<void> {
  await authFetch(`/connector/devices/${props.deviceId}/agents/${props.sessionId}/compact`, {
    method: 'POST',
  })
}

function connect(): void {
  // A configured CONNECTOR_WS_BASE routes the socket to a WebSocket-capable origin when
  // the page origin sits behind an edge that strips the WS Upgrade; empty (the default)
  // uses the page origin, unchanged. Only its origin matters — /connector is absolute.
  const fallback = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
  const url = new URL(`/connector/session/${encodeURIComponent(props.sessionId)}/screen`, CONNECTOR_WS_BASE || fallback)
  if (url.protocol === 'https:') url.protocol = 'wss:'
  else if (url.protocol === 'http:') url.protocol = 'ws:'
  const token = AccountService.accessToken
  if (token) url.searchParams.set('token', token)
  ws = new WebSocket(url.toString())
  ws.binaryType = 'arraybuffer'
  ws.onopen = () =>
    setTimeout(() => {
      sendSize(true)
      sendControl()
    }, 30)
  ws.onmessage = (ev) => {
    if (!term) return
    if (typeof ev.data === 'string') term.write(ev.data)
    else term.write(new Uint8Array(ev.data as ArrayBuffer))
  }
}

onMounted(async () => {
  try {
    await Promise.race([
      Promise.all([
        (document as Document).fonts.load('14px "JetBrainsMono Nerd Font"'),
        (document as Document).fonts.load('700 14px "JetBrainsMono Nerd Font"'),
      ]),
      new Promise((r) => setTimeout(r, 800)),
    ])
  } catch {
    /* fall back to monospace */
  }
  term = new Terminal({
    fontSize: fontSize.value,
    lineHeight: 1.1,
    fontFamily: '"JetBrainsMono Nerd Font", "JetBrains Mono", "Cascadia Code", monospace',
    theme: { background: '#000000', foreground: '#e7e3f0' },
    cursorBlink: true,
    allowProposedApi: true,
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  if (termEl.value) term.open(termEl.value)
  try {
    fit.fit()
  } catch {
    /* ignore */
  }
  // The dialog / new tab often has no layout yet when the terminal opens (→ 0×0
  // grid → blank). Re-fit + re-sync whenever the container actually gets a size,
  // so it renders on first open without needing a refresh.
  if (termEl.value && 'ResizeObserver' in window) {
    ro = new ResizeObserver(() => {
      try {
        fit?.fit()
      } catch {
        /* not laid out yet */
      }
      sendSize(true)
    })
    ro.observe(termEl.value)
  }

  // only 'full' types into the program
  term.onData((d) => {
    if (viewMode.value !== 'full') return
    if (ws && ws.readyState === 1) ws.send(enc.encode(d))
  })

  // wheel → PgUp/PgDn (Claude keeps its own scrollback)
  termEl.value?.addEventListener(
    'wheel',
    (e: WheelEvent) => {
      if (viewMode.value !== 'scroll' && viewMode.value !== 'full') return
      if (!ws || ws.readyState !== 1) return
      e.preventDefault()
      e.stopPropagation()
      ws.send(enc.encode(e.deltaY < 0 ? '\x1b[5~' : '\x1b[6~'))
      ws.send(JSON.stringify({ type: 'control', level: 'scroll' }))
    },
    { passive: false, capture: true }
  )

  connect()
  sizeTimer = window.setInterval(() => sendSize(false), 3000)
  window.addEventListener('resize', onResize)
})

function onResize(): void {
  sendSize(false)
}

onBeforeUnmount(() => {
  clearInterval(sizeTimer)
  ro?.disconnect()
  window.removeEventListener('resize', onResize)
  if (ws) {
    ws.close()
    ws = null
  }
  term?.dispose()
  term = null
})
</script>

<style scoped>
.st-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #000;
}
.st-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  background: #14121a;
  border-bottom: 1px solid #2b2740;
}
.st-spacer {
  flex: 1;
}
.st-mode {
  font-size: 12px;
  padding: 3px 9px;
  border: 0;
  border-radius: 5px;
  background: #2b2740;
  color: #cbc4e0;
  cursor: pointer;
}
.st-mode.on {
  background: #6b46c1;
  color: #fff;
}
.st-term {
  flex: 1;
  min-height: 0;
  padding: 6px;
}
</style>

<!-- Global @font-face (unscoped) so the bundled Nerd Font is registered for xterm's
     cell measurement — same fonts web-claude / GottyTerminal use. -->
<style>
@font-face {
  font-family: 'JetBrainsMono Nerd Font';
  font-weight: 400;
  font-display: swap;
  src: url('/fonts/JetBrainsMonoNF-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'JetBrainsMono Nerd Font';
  font-weight: 700;
  font-display: swap;
  src: url('/fonts/JetBrainsMonoNF-Bold.woff2') format('woff2');
}
</style>
