<script setup lang="ts">
// 界面导览的一站：目标控件四周一圈高亮，旁边一个浮动的小窗写这里是什么、怎么用。
// 导览里带「上一步 / 下一步」和第几站；第一次才出现的提示只有一颗「知道了」。
//
// 圈哪里由调用方给：页面上的一个元素（导览从 `v-guide-anchor` 的登记表里找到它），
// 或者一个固定的框。没有目标、或者目标量出来是 0×0（不在页面上），整块就不出现：
// 人离开了那一页，气泡跟着控件一起走。整层不吃指针，被圈着的控件照常能点；只有小窗
// 自己那一块收回指针。
//
// 小窗不盖住人要看、要点的东西：文字、能点能输入的控件、被圈的那一个都算。放哪由
// 这里一处定（`placeBubble`）：先试目标右边，再从目标一层层往外试上面和下面，都不行
// 就在整个窗口里找离目标最近的一块空地；真没有空地，挑压住东西最少的地方——导览
// 不能停在半路。
//
// 点一下常会带出一段动画（支线滑出来、任务页换进来、一段文字展开），动画中途量到的
// 位置是错的。页面还在动的时候圈跟着目标走，小窗原来那块地方被占了就先收起来，等
// 动画停了再落位；原来那块地方还空着就不挪，免得文字一长出来小窗满屏跳。
//
// 人觉得它挡着东西时可以拖：拖过之后这一站就停在他放的地方，走到下一站重新算。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
/** 在整个窗口里找空地时，每隔这么多像素试一个位置。 */
const GRID = 16
/** 人会去点、会去输入的控件。 */
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
}
interface Rect {
  left: number
  top: number
  right: number
  bottom: number
}
/** 小窗不能压住的东西，`weight` 是真压住时有多糟（挑压得最少的那个位置时用）。 */
interface Obstacle extends Rect {
  weight: number
}

const box = ref<Box | null>(null)
const spot = ref<Spot | null>(null)
/** 页面在动、小窗原来那块地方又被占了：先收起来，等停了再落位。 */
const parked = ref(false)
/** 小窗落位时目标在哪：目标挪了，小窗要跟着重新找地方。 */
let placedFor: DOMRectReadOnly | null = null
/** 人把它拖到哪了。拖过之后这一站就停在他放的地方，走到下一站再清掉重算。 */
const dragged = ref<{ left: number; top: number } | null>(null)
let dragFrom: { x: number; y: number; left: number; top: number; pointerId: number } | null = null
let bubbleNode: HTMLElement | null = null
let bubbleH = BUBBLE_H
function holdBubble(el: unknown) {
  bubbleNode = el instanceof HTMLElement ? el : null
}
/** 页面上还有没有在演的动画（过渡、入场）。一直转的（加载圈、「正在工作」的点）不算。 */
function animating(): boolean {
  return (document.getAnimations?.() ?? []).some((a) => {
    if (a.playState !== 'running') return false
    const effect = a.effect as KeyframeEffect | null
    if (effect?.getTiming().iterations === Infinity) return false
    const el = effect?.target
    return !(el instanceof Element && el.closest('.gb'))
  })
}

// 拖的是标题那一条：按住它挪，小窗跟着走。点在「下一步」上不算拖——那两个按钮不在
// 这一行里。挪过 3px 才算拖，单纯点一下标题不会把自动摆的位置钉住。
function startDrag(e: PointerEvent) {
  if (e.button !== 0 || !bubbleNode) return
  const r = bubbleNode.getBoundingClientRect()
  dragFrom = { x: e.clientX, y: e.clientY, left: r.left, top: r.top, pointerId: e.pointerId }
  try {
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  } catch {
    // 合成出来的事件、或者指针已经不在了：捕获不上也不影响挪。
  }
  e.preventDefault()
}
function moveDrag(e: PointerEvent) {
  if (!dragFrom || !bubbleNode) return
  // 另一个指头（多指触屏）的移动不算这一拖。
  if (e.pointerId !== dragFrom.pointerId) return
  const dx = e.clientX - dragFrom.x
  const dy = e.clientY - dragFrom.y
  if (!dragged.value && Math.abs(dx) + Math.abs(dy) < 3) return
  const w = bubbleNode.offsetWidth || BUBBLE_W
  const h = bubbleNode.offsetHeight || bubbleH
  dragged.value = {
    left: clamp(dragFrom.left + dx, EDGE, Math.max(EDGE, window.innerWidth - w - EDGE)),
    top: clamp(dragFrom.top + dy, EDGE, Math.max(EDGE, window.innerHeight - h - EDGE)),
  }
}
function endDrag(e: PointerEvent) {
  if (!dragFrom) return
  dragFrom = null
  try {
    ;(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId)
  } catch {
    // 没捕获住（浏览器已经自己收回了）就什么都不用做。
  }
}
// 走到下一站：人上一次拖的位置到这里为止，重新按新目标算一次。
watch(
  () => `${props.title}::${props.step ?? ''}`,
  () => {
    dragged.value = null
    dragFrom = null
  }
)
/** 窗口改小了之后，人拖到边上那一版也得跟着收回来，不然会跑到屏幕外面。 */
function clampDragged() {
  const d = dragged.value
  if (!d) return
  const w = bubbleNode?.offsetWidth || BUBBLE_W
  const h = bubbleNode?.offsetHeight || bubbleH
  const left = clamp(d.left, EDGE, Math.max(EDGE, window.innerWidth - w - EDGE))
  const top = clamp(d.top, EDGE, Math.max(EDGE, window.innerHeight - h - EDGE))
  if (left !== d.left || top !== d.top) dragged.value = { left, top }
}

function measure(): boolean {
  clampDragged()
  const target = props.target
  const el = target instanceof HTMLElement ? target : null
  const rect: DOMRectReadOnly | null = el ? el.getBoundingClientRect() : (target as DOMRectReadOnly | null)
  // 一个 0×0 的框没法指：它要么还没排完版，要么被 display:none 收起来了。
  if (!rect || (!rect.width && !rect.height)) {
    box.value = null
    return false
  }
  box.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    radius: el ? getComputedStyle(el).borderRadius || '' : '',
  }
  if (bubbleNode?.offsetHeight) bubbleH = bubbleNode.offsetHeight
  // 人已经把它拖到自己放的地方了：位置不再由这里挑，只要那圈跟着目标走。页面上还有
  // 动画在演时得下一帧再量一次，不然目标在动画里挪完，圈还停在动画开头那个位置。
  if (dragged.value) {
    parked.value = false
    return animating()
  }
  const obstacles = collectObstacles(rect)
  const moving = animating()
  const here = spot.value
  // 原来那块地方还站得住：谁也不压，而且整个小窗还在窗口里。窗口变小、或者这一步的
  // 字让小窗变高之后，原来那个位置可能已经出了边——那也得重新找，不能留着被裁掉。
  const clear =
    !!here &&
    here.left >= EDGE &&
    here.left + BUBBLE_W <= window.innerWidth - EDGE &&
    here.top >= EDGE &&
    here.top + bubbleH <= window.innerHeight - EDGE &&
    covers(obstacles, here.left, here.top, BUBBLE_W, bubbleH) === 0
  if (moving) {
    parked.value = !clear
    return true
  }
  // 停下来了：原来那块地方被占了，或者目标挪了地方（小窗得跟过去），才重新找。
  if (!clear || !placedFor || moved(placedFor, rect)) {
    spot.value = placeBubble(el, rect, obstacles)
    placedFor = rect
  }
  // 小窗还没画出来过：高度是估的，先藏着画一帧，量到真高度再落一次位。
  parked.value = !bubbleNode
  return !bubbleNode
}

function moved(a: DOMRectReadOnly, b: DOMRectReadOnly): boolean {
  return (
    Math.abs(a.left - b.left) > 1 ||
    Math.abs(a.top - b.top) > 1 ||
    Math.abs(a.width - b.width) > 1 ||
    Math.abs(a.height - b.height) > 1
  )
}

/** 窗口里看得见的文字（一行一个框）和控件，加上被圈的那一个。 */
function collectObstacles(rect: DOMRectReadOnly): Obstacle[] {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const out: Obstacle[] = []
  const add = (r: Rect, weight: number) => {
    if (r.right - r.left < 1 || r.bottom - r.top < 1) return
    if (r.right <= 0 || r.left >= vw || r.bottom <= 0 || r.top >= vh) return
    out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom, weight })
  }
  const visible = (el: Element) =>
    !el.closest('.gb') && (el.checkVisibility?.({ opacityProperty: true, visibilityProperty: true }) ?? true)
  if (props.ring) add(padded(rect, RING_PAD), 100)
  for (const c of document.querySelectorAll<HTMLElement>(CONTROLS)) {
    if (visible(c)) add(c.getBoundingClientRect(), 10)
  }
  // 滚出容器的那几行还排在那里，只是被裁掉了：按容器裁一下，不然空地会被看不见的字占掉。
  const clips = new Map<Element, Rect | null>()
  const clipOf = (el: Element | null): Rect | null => {
    if (!el || el === document.body) return null
    if (clips.has(el)) return clips.get(el) ?? null
    const outer = clipOf(el.parentElement)
    const style = getComputedStyle(el)
    let clip = outer
    if (style.overflowX !== 'visible' || style.overflowY !== 'visible') {
      const r = el.getBoundingClientRect()
      clip = outer ? intersect(outer, r) : r
    }
    clips.set(el, clip)
    return clip
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  const seen = new Map<Element, boolean>()
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement
    if (!parent || !node.textContent?.trim()) continue
    let shown = seen.get(parent)
    if (shown === undefined) seen.set(parent, (shown = visible(parent)))
    if (!shown) continue
    range.selectNodeContents(node)
    const clip = clipOf(parent)
    for (const line of range.getClientRects()) add(clip ? intersect(clip, line) : line, 1)
  }
  return out
}

/** 小窗放在 (left, top) 时压住的东西，按轻重加起来；0 就是一点没压。 */
function covers(obstacles: Obstacle[], left: number, top: number, w: number, h: number): number {
  let sum = 0
  for (const o of obstacles) {
    const x = Math.min(o.right, left + w) - Math.max(o.left, left)
    const y = Math.min(o.bottom, top + h) - Math.max(o.top, top)
    if (x > 0 && y > 0) sum += x * y * o.weight
  }
  return sum
}

/** 和这块范围相交的障碍物。找位置时只算挨得上的，页面上离得远的大批矩形就不算了。 */
function within(obstacles: Obstacle[], left: number, top: number, right: number, bottom: number): Obstacle[] {
  return obstacles.filter((o) => o.right > left && o.left < right && o.bottom > top && o.top < bottom)
}

function placeBubble(anchor: HTMLElement | null, rect: DOMRectReadOnly, obstacles: Obstacle[]): Spot {
  const w = BUBBLE_W
  const h = bubbleH
  const maxLeft = Math.max(EDGE, window.innerWidth - w - EDGE)
  const maxTop = Math.max(EDGE, window.innerHeight - h - EDGE)
  const aboveFirst = rect.top > window.innerHeight * BELOW_CUT
  const inWindow = (left: number, top: number) => left >= EDGE && left <= maxLeft && top >= EDGE && top <= maxTop
  const fits = (left: number, top: number) => inWindow(left, top) && covers(obstacles, left, top, w, h) === 0
  // 左栏里的目标（频道、总览、资料库）放在它右边：上下都是一排可点的行。
  const right = rect.right + BUBBLE_GAP
  const sideTop = clamp(rect.top + rect.height / 2 - h / 2, EDGE, maxTop)
  if (fits(right, sideTop)) return { left: right, top: sideTop }
  const left = clamp(rect.left + rect.width / 2 - w / 2, EDGE, maxLeft)
  // 从目标自己一层层往外（按钮、它那一行、它所在的输入框……）；固定的框只试它自己。
  const layers: DOMRectReadOnly[] = [rect]
  for (let el = anchor?.parentElement ?? null; el && el !== document.body; el = el.parentElement)
    layers.push(el.getBoundingClientRect())
  for (const r of layers) {
    if (!r.width && !r.height) continue
    const above = r.top - BUBBLE_GAP - h
    const below = r.bottom + BUBBLE_GAP
    for (const top of aboveFirst ? [above, below] : [below, above]) {
      if (fits(left, top)) return { left, top }
    }
  }
  // 目标四周都占满了：整个窗口里挑离目标最近的空地；没有空地就挑压得最少的。
  const near = (l: number, tp: number) => {
    const dx = Math.max(0, rect.left - (l + w), l - rect.right)
    const dy = Math.max(0, rect.top - (tp + h), tp - rect.bottom)
    return dx * dx + dy * dy
  }
  let best: Spot = {
    left,
    top: aboveFirst ? clamp(rect.top - BUBBLE_GAP - h, EDGE, maxTop) : clamp(rect.bottom + BUBBLE_GAP, EDGE, maxTop),
  }
  let bestCover = Infinity
  let bestNear = Infinity
  // 整个窗口按细格扫一遍：先挑压得最少的，一样少时挑离目标最近的。每一行只称和这一行
  // 相交的障碍物——只扫一条 126 px 高的带子，页面上别的字不参与——所以一趟细扫的开销
  // 由这一行的字数定，不是整页的行数。一行都没有可挑的（有地方可放）时，比当前最优更
  // 远的那些位置连算都不用算：压住的面积不会小于 0。
  for (let tp = EDGE; tp <= maxTop; tp += GRID) {
    const local = within(obstacles, EDGE, tp, maxLeft + w, tp + h)
    for (let l = EDGE; l <= maxLeft; l += GRID) {
      const d = near(l, tp)
      if (bestCover === 0 && d >= bestNear) continue
      const cover = covers(local, l, tp, w, h)
      if (cover < bestCover || (cover === bestCover && d < bestNear)) {
        best = { left: l, top: tp }
        bestCover = cover
        bestNear = d
      }
    }
  }
  return best
}

function padded(r: Rect, pad: number): Rect {
  return { left: r.left - pad, top: r.top - pad, right: r.right + pad, bottom: r.bottom + pad }
}
function intersect(a: Rect, b: Rect): Rect {
  return {
    left: Math.max(a.left, b.left),
    top: Math.max(a.top, b.top),
    right: Math.min(a.right, b.right),
    bottom: Math.min(a.bottom, b.bottom),
  }
}
function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

// 一帧最多量一次；页面还在动就下一帧接着量，停了为止。
let queued = 0
function measureSoon() {
  if (queued) return
  queued = requestAnimationFrame(() => {
    queued = 0
    if (measure()) measureSoon()
  })
}

// 换了一站就重新找地方；同一站里只在原来那块地方被占了才挪。
watch(
  () => [props.target, props.step, props.title],
  () => {
    spot.value = null
    placedFor = null
    if (measure()) measureSoon()
  },
  { immediate: true }
)

useEventListener(window, 'resize', measureSoon, { passive: true })
// 拖到一半指针不见了（切走窗口、捕获丢了、那一下 pointerup 没送到）：在窗口这一层也
// 收一次尾，不然过期的起点留着，指针之后从标题上划过，小窗会照它跳过去。
useEventListener(window, 'pointerup', () => {
  dragFrom = null
})
useEventListener(window, 'pointercancel', () => {
  dragFrom = null
})
// 捕获阶段：侧栏、房间各自的滚动在别的容器上，冒泡到 window 之前就被拦下了。
useEventListener(document, 'scroll', measureSoon, { capture: true, passive: true })
// 点一下、一段过渡开演：后面跟着的展开和滑入不改 DOM 结构，下面那个观察器看不到。
useEventListener(document, 'click', measureSoon, { capture: true, passive: true })
useEventListener(document, 'transitionrun', measureSoon, { capture: true, passive: true })
useEventListener(document, 'animationstart', measureSoon, { capture: true, passive: true })

// 页面上的东西变了也重放一次：小窗原来那块地方可能刚长出一行字、一排控件。
const mutations = new MutationObserver(measureSoon)
onMounted(() => mutations.observe(document.body, { childList: true, subtree: true, characterData: true }))
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
  if (dragged.value) return { left: `${dragged.value.left}px`, top: `${dragged.value.top}px` }
  const p = spot.value
  if (!p) return null
  return { left: `${p.left}px`, top: `${p.top}px` }
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
        :class="{ 'gb__bubble--parked': parked }"
        :style="bubbleStyle"
        role="dialog"
        :aria-label="title"
      >
        <div
          class="gb__head"
          :title="t('welcome.tour.dragHint')"
          @pointerdown="startDrag"
          @pointermove="moveDrag"
          @pointerup="endDrag"
          @pointercancel="endDrag"
        >
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
.gb__bubble--parked {
  visibility: hidden;
}
.gb__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  /* 这一条是拖动的手柄：光标说得出可以拖，手机上别把滚动抢走。 */
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.gb__head:active {
  cursor: grabbing;
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
