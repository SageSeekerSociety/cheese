<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useHost, watch } from 'vue'

import { announce } from './emit'
const props = withDefaults(
  defineProps<{
    count?: number
    value?: number
    labels?: string[]
    interval?: number
    loop?: boolean
    disabled?: boolean
  }>(),
  { count: 1, value: 0, interval: 1000 }
)
const host = useHost() as HTMLElement & { value?: unknown }
const last = computed(() => Math.max(0, Math.floor(props.count) - 1))
const at = computed(() => Math.min(Math.max(0, Math.floor(props.value ?? 0)), last.value))
const name = computed(() => props.labels?.[at.value] ?? `第 ${at.value + 1} / ${last.value + 1} 步`)
const playing = ref(false)
let timer: number | undefined
function go(step: number) {
  if (step === at.value) return
  announce(host, step)
}
/** Back or forward one step by hand: playback stops there. */
function step(by: number) {
  stop()
  go(at.value + by)
}
function tick() {
  if (at.value >= last.value) {
    if (props.loop) go(0)
    else stop()
    return
  }
  go(at.value + 1)
}
function stop() {
  playing.value = false
  window.clearInterval(timer)
}
function play() {
  if (playing.value) return stop()
  if (at.value >= last.value && !props.loop) go(0)
  playing.value = true
  timer = window.setInterval(tick, Math.max(200, props.interval))
}
watch(
  () => props.interval,
  () => {
    if (playing.value) {
      stop()
      play()
    }
  }
)
onBeforeUnmount(stop)
</script>
<template>
  <div class="player" role="group">
    <button type="button" :disabled="disabled || at <= 0" @click.stop="step(-1)">上一步</button>
    <button type="button" class="play" :disabled="disabled" :aria-pressed="playing" @click.stop="play">
      {{ playing ? '暂停' : '播放' }}
    </button>
    <button type="button" :disabled="disabled || at >= last" @click.stop="step(1)">下一步</button>
    <span class="where">{{ name }}</span>
    <span class="track" aria-hidden="true"
      ><span class="fill" :style="{ width: `${last ? (at / last) * 100 : 100}%` }"
    /></span>
  </div>
</template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: block;
  margin: 8px 0;
}
.player {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
button {
  min-height: 32px;
  padding: 4px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: var(--fill);
}
button:disabled {
  cursor: default;
  opacity: 0.5;
}
.play {
  min-width: 64px;
  border-color: var(--ink);
  background: var(--ink);
  color: var(--surface);
}
.play:hover:not(:disabled) {
  background: var(--text);
}
.where {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.track {
  flex: 1 1 120px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--line);
  overflow: hidden;
}
.fill {
  display: block;
  height: 100%;
  background: var(--ink);
  transition: width var(--dur-quick) var(--ease-standard);
}
button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
