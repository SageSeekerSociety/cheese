<script setup lang="ts">
import type { ShaderMount } from '@paper-design/shaders'
import type { SceneId } from './scenes'

import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { sceneForThisPage, SCENES } from './scenes'

import { useAppTheme } from '@/theme'

// The brand scene behind the sign-in pages (design-system §9.9). It keeps
// moving slowly and follows the pointer; under reduced motion it is one still
// frame. ShaderMount itself pauses while the tab is hidden or the pane is off
// screen. Without WebGL 2 the pane stays its plain ground.

const props = withDefaults(defineProps<{ narrow?: boolean; scene?: SceneId }>(), { narrow: false, scene: undefined })

const host = ref<HTMLElement | null>(null)
const shown = ref(false)
const scene = props.scene ?? sceneForThisPage()
const { isDark } = useAppTheme()

// Where the animation starts, in ms. A fixed start keeps the still frame under
// reduced motion the same every time, and a little in so nothing is mid-entrance.
const START_FRAME = 7000
// How quickly the scene catches up with the pointer, per frame.
const FOLLOW = 0.08

const reducedQuery = typeof window !== 'undefined' ? window.matchMedia?.('(prefers-reduced-motion: reduce)') : undefined

// A lost context gets one more try, a moment later: a GPU reset or a laptop
// waking up is usually over by then, and a GPU that keeps failing is left alone.
const RETRY_AFTER_LOSS = 1000
const TRIES_AFTER_LOSS = 1

let mount: ShaderMount | null = null
let generation = 0
let raf = 0
let stopPointer = () => {}
let losses = 0
let retry = 0

async function start() {
  const el = host.value
  if (!el) return
  const mine = ++generation
  const reduced = !!reducedQuery?.matches
  try {
    const P = await import('@paper-design/shaders')
    const pane = el.parentElement ?? el
    const spec = await SCENES[scene](P, {
      dark: isDark.value,
      back: getComputedStyle(pane).backgroundColor,
      narrow: props.narrow,
    })
    if (mine !== generation) return
    mount = new P.ShaderMount(
      el,
      spec.fragment,
      spec.uniforms,
      undefined,
      reduced ? 0 : spec.speed,
      START_FRAME,
      1,
      1600 * 1000,
      spec.mipmaps
    )
    el.querySelector('canvas')?.addEventListener('webglcontextlost', onContextLost, { once: true })
    shown.value = true
    if (!reduced) stopPointer = followPointer(pane, spec)
  } catch {
    // No WebGL 2, or a texture failed to load: the pane keeps its plain ground.
  }
}

// A canvas whose context is gone stays on top of the pane, blank, or painted
// by the browser as a crashed canvas. It goes, and the pane shows its plain
// ground until the scene can be drawn again.
function onContextLost() {
  shown.value = false
  stop()
  if (losses++ < TRIES_AFTER_LOSS) retry = window.setTimeout(start, RETRY_AFTER_LOSS)
}

function followPointer(pane: HTMLElement, spec: Awaited<ReturnType<(typeof SCENES)[SceneId]>>) {
  let x = 0
  let y = 0
  let targetX = 0
  let targetY = 0
  let inside = false
  const t0 = performance.now()

  const step = () => {
    raf = 0
    if (!mount) return
    x += (targetX - x) * FOLLOW
    y += (targetY - y) * FOLLOW
    mount.setUniforms(spec.pointer(x, y, (performance.now() - t0) / 1000))
    const settling = Math.abs(targetX - x) + Math.abs(targetY - y) > 0.002
    if ((inside || settling || spec.drift) && !document.hidden) raf = requestAnimationFrame(step)
  }
  const kick = () => {
    if (!raf) raf = requestAnimationFrame(step)
  }
  const onMove = (e: PointerEvent) => {
    const r = pane.getBoundingClientRect()
    targetX = ((e.clientX - r.left) / r.width) * 2 - 1
    targetY = ((e.clientY - r.top) / r.height) * 2 - 1
    inside = true
    kick()
  }
  const onLeave = () => {
    inside = false
    targetX = 0
    targetY = 0
    kick()
  }
  // A drifting scene stops with the tab and has to be started again.
  const onVisible = () => {
    if (!document.hidden && spec.drift) kick()
  }

  pane.addEventListener('pointermove', onMove)
  pane.addEventListener('pointerleave', onLeave)
  document.addEventListener('visibilitychange', onVisible)
  kick()
  return () => {
    pane.removeEventListener('pointermove', onMove)
    pane.removeEventListener('pointerleave', onLeave)
    document.removeEventListener('visibilitychange', onVisible)
  }
}

function stop() {
  generation++
  clearTimeout(retry)
  cancelAnimationFrame(raf)
  raf = 0
  stopPointer()
  stopPointer = () => {}
  mount?.dispose()
  mount = null
  host.value?.replaceChildren()
}

// The colors are baked into the scene, so a change of theme, of motion
// preference or of layout draws it again.
async function restart() {
  stop()
  await nextTick()
  start()
}

onMounted(() => {
  start()
  reducedQuery?.addEventListener?.('change', restart)
})
watch([isDark, () => props.narrow], restart)
onBeforeUnmount(() => {
  reducedQuery?.removeEventListener?.('change', restart)
  stop()
})
</script>

<template>
  <div ref="host" class="brand-scene" :class="{ 'brand-scene--shown': shown }" aria-hidden="true" />
</template>

<style scoped>
.brand-scene {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity var(--dur-slow) var(--ease-out);
}

.brand-scene--shown {
  opacity: 1;
}

.brand-scene :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

@media (prefers-reduced-motion: reduce) {
  .brand-scene {
    transition: none;
  }
}
</style>
