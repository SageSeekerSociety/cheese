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
//
// 连的是 `composables/useDeviceScreen.ts` 那一层：端点在 API 层，组件不直接碰
// （`.claude/rules/architecture.md` 的组件边界）。这里只有 xterm 和它的大小。
import '@xterm/xterm/css/xterm.css'

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'

import { useDeviceScreen } from '../composables/useDeviceScreen'
import { t } from '../i18n'

const props = defineProps<{ sid: string; readonly?: boolean }>()

const host = ref<HTMLDivElement | null>(null)
let term: Terminal | null = null
let fit: FitAddon | null = null
let ro: ResizeObserver | null = null

const { status, connect, send, isOpen } = useDeviceScreen(() => props.sid, {
  // 屏幕数据是字节、控制帧是字符串，xterm 自己分得清。
  onData: (chunk) => term?.write(chunk),
  // first sized frame attaches this viewer on the backend
  onAttached: () => fitAndResize(),
  // 换到另一台屏幕：上一台的画面不能留着。
  onSwitched: () => term?.reset(),
})

function sendResize(): void {
  if (!term || !isOpen()) return
  send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
}

function fitAndResize(): void {
  try {
    fit?.fit()
  } catch {
    // fit throws if the element has no layout yet — ignore, a later tick retries.
  }
  sendResize()
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
      if (isOpen()) send(enc.encode(d))
    })
  }
  fitAndResize()
  ro = new ResizeObserver(() => fitAndResize())
  ro.observe(host.value)
  connect(props.sid)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  term?.dispose()
  term = null
  fit = null
})
</script>

<template>
  <div class="device-live">
    <div v-if="status !== 'open'" class="device-live__status">
      {{ status === 'connecting' ? t('work.liveView.connecting') : t('work.liveView.closed') }}
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
  z-index: var(--z-raised);
  font-size: 12px;
  /* Sits ON the invariant terminal ground above, so it is light-on-dark in both
     themes — tokens here would make it dark-on-dark in the light theme. */
  color: #9aa0a6;
  background: rgba(0, 0, 0, 0.5);
  padding: 2px 8px;
  border-radius: var(--radius-lg);
}
</style>
