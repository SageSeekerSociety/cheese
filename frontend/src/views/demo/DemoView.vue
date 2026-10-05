<script setup lang="ts">
// /demo/<名字>：文档里的动态演示（自己一个入口，见 src/demo-main.ts）。两种用法：
//
//   - 直接打开：自己带播放条，放完一步接下一步。
//   - 文档嵌进 iframe（?embed=1）：没有播放条，听文档那边的步骤条。文档发
//     { cheeseDemo: 'go', step, play }，这边放完那一步回 { cheeseDemo: 'done', step }，
//     下一步放不放由文档决定 —— 两边只有一个人在数步数。
import type { Scene } from './demoScene'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { installDemoBackend } from './demoBackend'
import DemoRoom from './DemoRoom.vue'
import { frameAt, stepDuration } from './demoScene'
import { SCENES } from './scenes'

import BaseButton from '@/components/base/BaseButton.vue'
import { docsUrl } from '@/lib/docsSite'

// 地址是 /demo/<名字>；只写 /demo（或者话题预览打开的根路径）就放第一个。
// 入口（demo-main.ts）把地址当 props 传进来，而不是这里自己读 location：测试里
// 换得了 props，换不了 happy-dom 的 location。
installDemoBackend()

const props = withDefaults(defineProps<{ path?: string; search?: string }>(), { path: '/', search: '' })
const params = new URLSearchParams(props.search)
const embedded = params.get('embed') === '1'
// The docs page that embeds this one: the docs' own host, or this origin when
// the platform serves them under /docs/. Messages go only there and are taken
// only from there.
const docsOrigin = new URL(docsUrl(), window.location.origin).origin
const fromPath = /^\/demo\/([\w-]+)/.exec(props.path)?.[1]
const name = ref(fromPath ?? params.get('scene') ?? Object.keys(SCENES)[0])
const scene = computed<Scene | null>(() => SCENES[name.value] ?? null)

// 直接打开时顶上能换一个演示看，地址跟着换，刷新还在同一个。
function pick(next: string): void {
  name.value = next
  history.replaceState(null, '', `/demo/${next}`)
  go(0, true)
}
const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

const step = ref(0)
const elapsed = ref(0)
const playing = ref(false)

const total = computed(() => scene.value?.steps.length ?? 0)
const duration = computed(() => (scene.value ? stepDuration(scene.value.steps[step.value]) : 0))
const frame = computed(() => (scene.value ? frameAt(scene.value, step.value, elapsed.value) : null))

let raf = 0
let last = 0

function tell(message: Record<string, unknown>): void {
  if (embedded && window.parent !== window)
    window.parent.postMessage({ cheeseDemo: message.type, ...message }, docsOrigin)
}

function loop(now: number): void {
  raf = 0
  if (!playing.value) return
  elapsed.value += now - last
  last = now
  if (elapsed.value >= duration.value) {
    elapsed.value = duration.value
    if (embedded) {
      playing.value = false
      tell({ type: 'done', step: step.value })
      return
    }
    if (step.value + 1 >= total.value) {
      playing.value = false
      return
    }
    step.value += 1
    elapsed.value = 0
  }
  raf = requestAnimationFrame(loop)
}

function play(): void {
  if (playing.value) return
  if (step.value + 1 >= total.value && elapsed.value >= duration.value) {
    step.value = 0
    elapsed.value = 0
  }
  if (reduced) {
    elapsed.value = duration.value
    return
  }
  playing.value = true
  last = performance.now()
  raf = requestAnimationFrame(loop)
}

function pause(): void {
  playing.value = false
  cancelAnimationFrame(raf)
  raf = 0
}

// 跳到某一步：要放就从这一步开头放，不放就停在这一步放完的样子。
function go(n: number, andPlay: boolean): void {
  pause()
  step.value = Math.max(0, Math.min(total.value - 1, n))
  elapsed.value = andPlay ? 0 : duration.value
  if (andPlay) play()
}

function onMessage(e: MessageEvent): void {
  if (e.origin !== docsOrigin) return
  const data = e.data as { cheeseDemo?: string; step?: number; play?: boolean } | null
  if (!data || data.cheeseDemo !== 'go' || typeof data.step !== 'number') return
  go(data.step, data.play === true)
}

onMounted(() => {
  if (embedded) {
    window.addEventListener('message', onMessage)
    // 文档那边还没发话之前，停在第一步放完的样子：画面不空着。
    go(0, false)
    tell({ type: 'ready', steps: total.value })
  } else {
    go(0, true)
  }
})

onBeforeUnmount(() => {
  pause()
  window.removeEventListener('message', onMessage)
})
</script>

<template>
  <div class="demo-page" :class="{ 'demo-page-embed': embedded }">
    <template v-if="scene && frame">
      <header v-if="!embedded" class="demo-head">
        <nav class="demo-pick">
          <button
            v-for="(s, key) in SCENES"
            :key="key"
            type="button"
            class="demo-pick-item"
            :class="{ 'demo-pick-on': key === name }"
            @click="pick(key)"
          >
            {{ s.title }}
          </button>
        </nav>
        <div class="demo-ctl">
          <BaseButton icon="mdi-chevron-left" size="sm" aria-label="上一步" @click="go(step - 1, false)" />
          <BaseButton
            kind="primary"
            size="sm"
            :prepend-icon="playing ? 'mdi-pause' : 'mdi-play'"
            @click="playing ? pause() : play()"
          >
            {{ playing ? '暂停' : '播放' }}
          </BaseButton>
          <BaseButton icon="mdi-chevron-right" size="sm" aria-label="下一步" @click="go(step + 1, false)" />
        </div>
      </header>
      <ol v-if="!embedded" class="demo-steps">
        <li v-for="(s, i) in scene.steps" :key="i">
          <button
            type="button"
            class="demo-step"
            :class="{ 'demo-step-on': i === step, 'demo-step-done': i < step }"
            @click="go(i, true)"
          >
            <span class="demo-step-bar"
              ><i :style="{ width: i < step ? '100%' : i === step ? `${(elapsed / duration) * 100}%` : '0%' }"
            /></span>
            <span class="demo-step-label">{{ i + 1 }}. {{ s.label }}</span>
          </button>
        </li>
      </ol>
      <div class="demo-stage">
        <!-- 换一个演示就换一间房：右边那几格挂上过就一直挂着（页签切来切去不重挂），
             换了剧本不重挂的话，上一间房的改动、预览、现场都会留在格子里面。 -->
        <DemoRoom :key="name" :scene="scene" :frame="frame" />
      </div>
    </template>
    <p v-else class="demo-missing">没有这个演示。</p>
  </div>
</template>

<style scoped>
.demo-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100vh;
  padding: 16px 24px 24px;
  background: var(--canvas);
}

.demo-page-embed {
  padding: 0;
  background: transparent;
}

.demo-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.demo-pick {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.demo-pick-item {
  padding: 4px 12px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.demo-pick-on {
  font-weight: 600;
  color: var(--ink);
  background: var(--fill);
}

.demo-ctl {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-left: auto;
}

.demo-steps {
  display: flex;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.demo-steps li {
  flex: 1;
  min-width: 0;
}

.demo-step {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  padding: 0;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.demo-step-bar {
  display: block;
  height: 3px;
  overflow: hidden;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}

.demo-step-bar i {
  display: block;
  height: 100%;
  background: var(--accent);
}

.demo-step-label {
  overflow: hidden;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.demo-step-on .demo-step-label {
  color: var(--ink);
}

.demo-step-done .demo-step-label {
  color: var(--muted);
}

.demo-stage {
  flex: 1;
  min-height: 0;
}

.demo-missing {
  margin: auto;
  color: var(--muted);
}
</style>
