<script setup lang="ts">
// 界面导览的一站：目标控件四周一圈高亮，旁边一个浮动的小窗写这里是什么、怎么用。
// 导览里带「上一步 / 下一步」和第几站；第一次才出现的提示只有一颗「知道了」。
//
// 圈哪里由调用方给：页面上的一个元素（导览从 `v-guide-anchor` 的登记表里找到它），
// 或者一个固定的框。没有目标、或者目标量出来是 0×0（不在页面上），整块就不出现：
// 人离开了那一页，气泡跟着控件一起走。整层不吃指针，被圈着的控件照常能点；只有小窗
// 自己那一块收回指针。
//
// 小窗尽量不盖住能点、能输入的东西——被圈的控件、它所在的输入框都算。放哪由这里
// 一处定（`placeBubble`）：先试目标右边，再从目标一层层往外试上面和下面；哪儿都会
// 压住点什么时照样放在目标旁边，导览不能停在半路。
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from 'vue'
import { useEventListener } from '@vueuse/core'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 要圈的东西：页面上的一个元素，或者一个固定的框（视口坐标）。 */
    target: HTMLElement | DOMRectReadOnly | null
    title: string
    text: string
    /** 导览里的第几站（从 1 数）；不给就是一条单独的提示。 */
    step?: number
    total?: number
    /** 画不画那一圈高亮。指着一大块区域（对话区、任务页）时不画，只放小窗。 */
    ring?: boolean
    /** 这一站有没有「下一步」。要人去做的那几站没有：做到了自己往下走。 */
    canNext?: boolean
  }>(),
  { ring: true, canNext: true, step: undefined, total: undefined }
)
const emit = defineEmits<{ back: []; next: []; skip: []; dismiss: [] }>()

const RING_PAD = 6
const BUBBLE_GAP = 10
const BUBBLE_W = 280
/** 小窗还没画出来、量不到高度时按这么高算。 */
const BUBBLE_H = 120
/** 目标在这个高度以下时，小窗先试它上面，免得掉出屏幕。 */
const BELOW_CUT = 0.55
const EDGE = 12
/** 小窗不能压住的东西：人会去点、会去输入的控件。 */
const CONTROLS =
  'button, a[href], input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="button"], [role="option"], [role="menuitem"]'

interface Box {
  left: number
  top: number
  width: number
  height: number
  radius: string
}
interface Spot {
  left: number
  top: number
  above: boolean
}

const box = ref<Box | null>(null)
const spot = ref<Spot | null>(null)
let bubbleNode: HTMLElement | null = null
let bubbleH = BUBBLE_H
function holdBubble(el: unknown) {
  bubbleNode = el instanceof HTMLElement ? el : null
}
function measure() {
  const target = props.target
  const el = target instanceof HTMLElement ? target : null
  const rect: DOMRectReadOnly | null = el ? el.getBoundingClientRect() : (target as DOMRectReadOnly | null)
  // 一个 0×0 的框没法指：它要么还没排完版，要么被 display:none 收起来了。
  if (!rect || (!rect.width && !rect.height)) {
    box.value = null
    return
  }
  box.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    radius: el ? getComputedStyle(el).borderRadius || '' : '',
  }
  spot.value = placeBubble(el, rect)
}

function clear(a: DOMRect, left: number, top: number, w: number, h: number): boolean {
  return a.right <= left || left + w <= a.left || a.bottom <= top || top + h <= a.top
}

function placeBubble(anchor: HTMLElement | null, rect: DOMRectReadOnly): Spot {
  const w = BUBBLE_W
  if (bubbleNode?.offsetHeight) bubbleH = bubbleNode.offsetHeight
  const h = bubbleH
  const aboveFirst = rect.top > window.innerHeight * BELOW_CUT
  const controls = [...document.querySelectorAll<HTMLElement>(CONTROLS)]
    .filter(
      (c) => !c.closest('.gb') && (c.checkVisibility?.({ opacityProperty: true, visibilityProperty: true }) ?? true)
    )
    .map((c) => c.getBoundingClientRect())
    .filter((r) => r.width > 0 && r.height > 0)
  const inWindow = (left: number, top: number) =>
    left >= EDGE && left + w <= window.innerWidth - EDGE && top >= EDGE && top + h <= window.innerHeight - EDGE
  const fits = (left: number, top: number) => inWindow(left, top) && controls.every((c) => clear(c, left, top, w, h))
  // 左栏里的目标（频道、总览、资料库）放在它右边：上下都是一排可点的行。
  const right = rect.right + BUBBLE_GAP
  const sideTop = clamp(rect.top + rect.height / 2 - h / 2, EDGE, window.innerHeight - h - EDGE)
  if (fits(right, sideTop)) return { left: right, top: sideTop, above: false }
  const left = clamp(rect.left + rect.width / 2 - w / 2, EDGE, Math.max(EDGE, window.innerWidth - w - EDGE))
  // 从目标自己一层层往外（按钮、它那一行、它所在的输入框……）；固定的框只试它自己。
  const layers: DOMRectReadOnly[] = [rect]
  for (let el = anchor?.parentElement ?? null; el && el !== document.body; el = el.parentElement)
    layers.push(el.getBoundingClientRect())
  for (const r of layers) {
    if (!r.width && !r.height) continue
    const above = r.top - BUBBLE_GAP
    const below = r.bottom + BUBBLE_GAP
    for (const up of aboveFirst ? [true, false] : [false, true]) {
      if (up ? fits(left, above - h) : fits(left, below)) return { left, top: up ? above : below, above: up }
    }
  }
  // 哪儿都会压住点什么：照样放出来，贴在目标右边（放不下就上面或下面）。导览不能
  // 因为找不到空地就停在半路——人看不到「下一步」，整条导览就卡死了。
  if (inWindow(right, sideTop)) return { left: right, top: sideTop, above: false }
  return aboveFirst
    ? { left, top: Math.max(rect.top - BUBBLE_GAP, h + EDGE), above: true }
    : { left, top: Math.min(rect.bottom + BUBBLE_GAP, window.innerHeight - h - EDGE), above: false }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

watchEffect(() => {
  void props.target
  void props.step
  measure()
})

useEventListener(window, 'resize', measure, { passive: true })
// 捕获阶段：侧栏、房间各自的滚动在别的容器上，冒泡到 window 之前就被拦下了。
useEventListener(document, 'scroll', measure, { capture: true, passive: true })

// 页面上的东西变了也重放一次：小窗原来那块地方可能刚长出一排控件。一帧最多量一次。
let queued = 0
function measureSoon() {
  if (queued) return
  queued = requestAnimationFrame(() => {
    queued = 0
    measure()
  })
}
const mutations = new MutationObserver(measureSoon)
onMounted(() => mutations.observe(document.body, { childList: true, subtree: true }))
onBeforeUnmount(() => {
  mutations.disconnect()
  cancelAnimationFrame(queued)
})

const ringStyle = computed(() => {
  const b = box.value
  if (!b) return null
  return {
    left: `${b.left - RING_PAD}px`,
    top: `${b.top - RING_PAD}px`,
    width: `${b.width + RING_PAD * 2}px`,
    height: `${b.height + RING_PAD * 2}px`,
    ...(b.radius ? { borderRadius: b.radius } : {}),
  }
})
const bubbleStyle = computed(() => {
  const p = spot.value
  if (!p) return null
  return p.above
    ? { left: `${p.left}px`, top: `${p.top}px`, transform: 'translateY(-100%)' }
    : { left: `${p.left}px`, top: `${p.top}px` }
})
const last = computed(() => !!props.total && props.step === props.total)
</script>

<template>
  <!-- 挂到 <body> 上：换页时内容区演一个带 transform 的动画，fixed 的定位基准会被
       祖先的 transform 换掉，留在原地的话小窗会跟着横着挪一下。 -->
  <Teleport to="body">
    <section v-if="box && ringStyle" :key="title" class="gb" :aria-label="t('welcome.tour.aria')">
      <div v-if="ring" class="gb__ring" :style="ringStyle" aria-hidden="true" />
      <div
        v-if="bubbleStyle"
        :ref="holdBubble"
        class="gb__bubble"
        :style="bubbleStyle"
        role="dialog"
        :aria-label="title"
      >
        <div class="gb__head">
          <span class="t-title">{{ title }}</span>
          <span v-if="total" class="t-meta">{{ step }}/{{ total }}</span>
        </div>
        <p class="gb__text">{{ text }}</p>
        <div class="gb__foot">
          <template v-if="total">
            <button type="button" class="gb__quiet" @click="emit('skip')">{{ t('welcome.tour.skip') }}</button>
            <span class="gb__go">
              <button v-if="(step ?? 1) > 1" type="button" class="gb__quiet" @click="emit('back')">
                {{ t('welcome.tour.back') }}
              </button>
              <button v-if="canNext" type="button" class="gb__primary" @click="emit('next')">
                {{ last ? t('welcome.tour.done') : t('welcome.tour.next') }}
              </button>
              <span v-else class="gb__auto">{{ t('welcome.tour.auto') }}</span>
            </span>
          </template>
          <button v-else type="button" class="gb__primary gb__end" @click="emit('dismiss')">
            {{ t('welcome.tour.gotIt') }}
          </button>
        </div>
      </div>
    </section>
  </Teleport>
</template>

<style scoped>
/* 整层不吃指针：它只画一圈高亮和一个小窗，被圈的控件要照常能点。 */
.gb {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  pointer-events: none;
}
.gb__ring {
  position: fixed;
  border-radius: var(--radius-lg);
  /* 一圈实线加一圈淡光：实线是说「就是这里」，淡光把视线从满屏内容里收过去。 */
  box-shadow:
    0 0 0 2px var(--accent),
    0 0 0 8px color-mix(in srgb, var(--accent) 18%, transparent);
  animation: gb-in var(--dur-base) var(--ease-out);
}
.gb__bubble {
  position: fixed;
  box-sizing: border-box;
  width: 280px;
  padding: 12px 14px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  animation: gb-in var(--dur-base) var(--ease-out);
}
.gb__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.gb__text {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.gb__foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
}
.gb__go {
  display: flex;
  gap: 6px;
}
.gb__quiet,
.gb__primary {
  border: none;
  border-radius: var(--radius-sm);
  padding: 3px 10px;
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
}
.gb__quiet {
  background: none;
  color: var(--faint);
}
.gb__quiet:hover {
  color: var(--muted);
}
.gb__primary {
  background: var(--ink);
  color: var(--surface);
  font-weight: 600;
}
.gb__auto {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  align-self: center;
}
.gb__end {
  margin-left: auto;
}
@keyframes gb-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>
