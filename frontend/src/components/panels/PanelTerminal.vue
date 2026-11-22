<script setup lang="ts">
// One shell's screen: an xterm in the 终端 tab. It draws what it is handed and says what is
// typed into it; where the bytes come from and go to is the host's (`work/TerminalHost.vue`).
// Images a program draws show inline, and a web address in the output opens in a new tab.
// The screen keeps the panel's own surface and type (the approved prototype draws the
// terminal on `--surface` in the mono face), so it follows the theme like the rest of the panel.
import '@xterm/xterm/css/xterm.css'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { FitAddon } from '@xterm/addon-fit'
import { ImageAddon } from '@xterm/addon-image'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { Terminal } from '@xterm/xterm'

const props = defineProps<{
  /** Said in place of the screen (no shell to show: being prepared, gone, refused). */
  placeholder?: string | null
  /** Said under what the shell wrote (it has ended). */
  note?: string | null
  /** The panel is on screen: it takes the keyboard. */
  active?: boolean
}>()
const emit = defineEmits<{
  type: [data: string]
  resize: [size: { cols: number; rows: number }]
}>()

const host = ref<HTMLDivElement | null>(null)
let term: Terminal | null = null
let fit: FitAddon | null = null
let resizeObserver: ResizeObserver | null = null
let themeObserver: MutationObserver | null = null

const ANSI = [
  'black',
  'red',
  'green',
  'yellow',
  'blue',
  'magenta',
  'cyan',
  'white',
  'brightBlack',
  'brightRed',
  'brightGreen',
  'brightYellow',
  'brightBlue',
  'brightMagenta',
  'brightCyan',
  'brightWhite',
] as const
/**
 * Images a program draws (Sixel, the iTerm2 inline protocol) are kept within a bound, per
 * terminal: one image is at most 2048 x 2048 pixels and its sequence at most 8 MB, and what all
 * of them hold together is at most 16 MB, the oldest dropped first. A shell that prints image
 * after image cannot grow the page without end.
 */
const IMAGES = {
  pixelLimit: 2048 * 2048,
  storageLimit: 16,
  sixelSizeLimit: 8_000_000,
  iipSizeLimit: 8_000_000,
}

/** A link in the output opens in a new tab, cut off from this page; only a web address does. */
function openLink(_event: MouseEvent, uri: string) {
  let url: URL
  try {
    url = new URL(uri)
  } catch {
    return
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return
  window.open(url.href, '_blank', 'noopener,noreferrer')
}

const kebab = (name: string) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function theme() {
  return {
    background: token('--surface'),
    foreground: token('--text'),
    cursor: token('--ink'),
    cursorAccent: token('--surface'),
    selectionBackground: token('--accent-wash'),
    // The 16 colours a program asks for, legible on the panel's surface in either theme.
    ...Object.fromEntries(ANSI.map((name) => [name, token(`--terminal-ansi-${kebab(name)}`)])),
  }
}

function refit() {
  if (!term || !fit || !host.value?.offsetParent) return
  const before = `${term.cols}x${term.rows}`
  try {
    fit.fit()
  } catch {
    return
  }
  if (`${term.cols}x${term.rows}` !== before) emit('resize', { cols: term.cols, rows: term.rows })
}

onMounted(() => {
  if (!host.value) return
  term = new Terminal({
    fontFamily: token('--font-mono') || 'monospace',
    fontSize: 13,
    cursorBlink: true,
    theme: theme(),
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.loadAddon(new ImageAddon(IMAGES))
  term.loadAddon(new WebLinksAddon(openLink))
  term.open(host.value)
  term.onData((data) => emit('type', data))
  refit()
  resizeObserver = new ResizeObserver(() => refit())
  resizeObserver.observe(host.value)
  themeObserver = new MutationObserver(() => {
    if (term) term.options.theme = theme()
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  if (props.active) term.focus()
})

watch(
  () => props.active,
  (active) => {
    if (active) {
      refit()
      term?.focus()
    }
  }
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  term?.dispose()
  term = null
  fit = null
})

defineExpose({
  /** Draw the shell's bytes; `done` once they are on screen. */
  write(bytes: Uint8Array, done: () => void) {
    if (term) term.write(bytes, done)
    else done()
  },
  /** Start the screen again from nothing. */
  reset() {
    term?.reset()
  },
  /** The screen's size in cells. */
  size() {
    return { cols: term?.cols ?? 80, rows: term?.rows ?? 24 }
  },
})
</script>

<template>
  <div class="term">
    <div ref="host" class="term__screen" :class="{ 'term__screen--off': !!placeholder }" />
    <div v-if="placeholder" class="term__ph">{{ placeholder }}</div>
    <div v-else-if="note" class="term__note">{{ note }}</div>
  </div>
</template>

<style scoped>
.term {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--surface);
}
.term__screen {
  flex: 1;
  min-height: 0;
  padding: 8px 12px 12px;
  cursor: text;
}
.term__screen--off {
  visibility: hidden;
}
.term__ph {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 40px;
  color: var(--muted);
  font-size: 13px;
  text-align: center;
}
.term__note {
  flex: none;
  padding: 2px 12px 8px;
  color: var(--muted);
  font-size: 13px;
}
</style>
