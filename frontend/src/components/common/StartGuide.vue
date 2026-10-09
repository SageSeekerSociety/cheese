<script setup lang="ts">
// 新用户那条手把手引导：一次只亮一步——目标按钮四周一圈高亮，旁边一条气泡写一句
// 要做什么，带「跳过引导」。
//
// 这里没有「下一步」按钮，也没有定时器：`step` 是外面按真实状态算出来的（哪一步还
// 没做），人真去做了，事实一变，`step` 自己往前走。这个组件只负责把当前这一步画在
// 该指的那颗按钮旁边。
//
// 目标找不到就整块不出现（`anchors` 里没有一个登记在页面上）。于是「人已经离开那
// 一步所在的页面」不用谁来判——按钮跟着页面走，气泡就跟着按钮走。
//
// 整层不吃指针：被指着的那颗按钮要能照常点到，只有气泡自己那一块把指针收回来。
//
// 气泡不盖住任何能点、能输入的东西——被指着的那颗按钮、它所在的那只输入框、芝士
// 刚问完的那几个选项都算。放哪由这里一处定，见 `placeBubble`：调用处不用各自躲。
import type { StartStepKey } from '@/lib/startGuide'

import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from 'vue'
import { useEventListener } from '@vueuse/core'

import { guideAnchor, useGuideAnchorRevision } from '@/composables/useStartGuide'

import { t } from '@/i18n'

const props = defineProps<{
  step: StartStepKey
  /** 这一步指的按钮。按顺序取第一个登记在页面上的那颗。 */
  anchors: string[]
  /** 气泡那句话里要用到的名字：第 2 步说的是项目里那位 AI 队友。 */
  agent?: string
}>()
const emit = defineEmits<{ (e: 'skip'): void }>()

/**
 * 每一步气泡上那句话。有五步、四句话是从「开始清单」那一行原样搬过来的：气泡本来
 * 就是清单上还没做掉的那一行，两处各写一句迟早会说岔。只有建项目和放材料这两句是
 * 引导自己的——清单上没有「建项目」这一行，而材料那一句要交代「也能放进资料库」，
 * 那是清单行标签说不下的话。
 */
const COPY: Record<StartStepKey, string> = {
  project: 'work.startGuide.project',
  talk: 'work.room.gettingStarted.talk',
  materials: 'work.startGuide.materials',
  repo: 'work.room.gettingStarted.repo',
  people: 'work.room.gettingStarted.people',
}

/** 高亮圈离按钮多远。贴紧了像是按钮自己的描边，离远了又指不准。 */
const RING_PAD = 6
const BUBBLE_GAP = 10
const BUBBLE_W = 240
/** 气泡还没画出来、量不到高度时按这么高算：一行字加一颗「跳过引导」。 */
const BUBBLE_H = 72
/** 目标在这个高度以下时，气泡先试它上面，免得掉出屏幕。 */
const BELOW_CUT = 0.55
/** 离窗口边缘至少这么远。 */
const EDGE = 12
/** 气泡不能压住的东西：人会去点、会去输入的控件。 */
const CONTROLS =
  'button, a[href], input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="button"], [role="option"], [role="menuitem"]'

interface Box {
  left: number
  top: number
  width: number
  height: number
  /** 目标自己的圆角。圈照抄它，才不会被一个圆按钮套上方的框。 */
  radius: string
}

interface Spot {
  left: number
  /** 气泡在目标上面时，这是气泡的底边（样式里往上挪一个自身高度）。 */
  top: number
  above: boolean
}

const box = ref<Box | null>(null)
/** 气泡放哪；null 是哪儿都会压住东西，这一步只画圈。 */
const spot = ref<Spot | null>(null)
// 气泡自己那块元素和它量出来的高度。不放进响应式里：放哪要用它的高度，它画不画又
// 看放在哪，两头互相触发会来回翻。
let bubbleNode: HTMLElement | null = null
let bubbleH = BUBBLE_H
function holdBubble(el: unknown) {
  bubbleNode = el instanceof HTMLElement ? el : null
}
const revision = useGuideAnchorRevision()

function locate(): HTMLElement | null {
  for (const name of props.anchors) {
    const el = guideAnchor(name)
    if (el) return el
  }
  return null
}

function measure() {
  const el = locate()
  const rect = el?.getBoundingClientRect()
  // 一个 0×0 的框没法指：它要么还没排完版，要么被 display:none 收起来了。
  if (!el || !rect || (!rect.width && !rect.height)) {
    box.value = null
    return
  }
  box.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    radius: getComputedStyle(el).borderRadius || '',
  }
  spot.value = placeBubble(el, rect)
}

function clear(a: DOMRect, left: number, top: number, w: number, h: number): boolean {
  return a.right <= left || left + w <= a.left || a.bottom <= top || top + h <= a.top
}

/**
 * 气泡放在哪：从目标自己往外一层层地试（按钮、它那一行、它所在的输入框……），
 * 每一层试上面和下面，取第一个整块在窗口里、又不压住任何控件的位置。
 *
 * 只贴着目标放的话，指着输入框角上那颗回形针时，气泡正好落在输入框打字的那一块上
 * ——人照着气泡要做的下一件事就是往那儿打字。往外退到「整只输入框的上面」才让开。
 * 哪一层都放不下（手机上，输入框上面正是芝士刚问的那几个选项）就不放：圈还在，
 * 指的东西不变，宁可少一句话也不挡住人要点的东西。
 */
function placeBubble(anchor: HTMLElement, rect: DOMRect): Spot | null {
  const w = BUBBLE_W
  if (bubbleNode?.offsetHeight) bubbleH = bubbleNode.offsetHeight
  const h = bubbleH
  const left = clamp(rect.left + rect.width / 2 - w / 2, EDGE, Math.max(EDGE, window.innerWidth - w - EDGE))
  const aboveFirst = rect.top > window.innerHeight * BELOW_CUT
  const controls = [...document.querySelectorAll<HTMLElement>(CONTROLS)]
    .filter(
      (c) => !c.closest('.sg') && (c.checkVisibility?.({ opacityProperty: true, visibilityProperty: true }) ?? true)
    )
    .map((c) => c.getBoundingClientRect())
    .filter((r) => r.width > 0 && r.height > 0)
  const fits = (top: number) =>
    top >= EDGE && top + h <= window.innerHeight - EDGE && controls.every((c) => clear(c, left, top, w, h))

  for (let el: HTMLElement | null = anchor; el && el !== document.body; el = el.parentElement) {
    const r = el === anchor ? rect : el.getBoundingClientRect()
    if (!r.width && !r.height) continue
    const above = r.top - BUBBLE_GAP
    const below = r.bottom + BUBBLE_GAP
    for (const up of aboveFirst ? [true, false] : [false, true]) {
      if (up ? fits(above - h) : fits(below)) return { left, top: up ? above : below, above: up }
    }
  }
  return null
}

// 目标会随着页面变（换页、菜单开合、rail 加载完、气泡自己换了下一步）：登记表一改
// 就重新找一次。读一眼 anchors 是因为调用处换了要指的东西。
watchEffect(() => {
  void revision.value
  void props.anchors.join()
  measure()
})

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

useEventListener(window, 'resize', measure, { passive: true })
// 捕获阶段：rail 那条自己的滚动、房间里的滚动都在别的容器上，冒泡到 window 之前
// 就被人拦下了。
useEventListener(document, 'scroll', measure, { capture: true, passive: true })

// 页面上的东西变了（芝士问完一道题、选项刚画出来，清单卡收起）也重放一次：气泡原来
// 那块地方可能刚长出一排选项。一帧最多量一次——流式输出时这里每帧都会被叫到。
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
    // 量不出来（或本来就是直角）就留给样式表里那条 `--radius-lg`。
    ...(b.radius ? { borderRadius: b.radius } : {}),
  }
})

// 横向跟按钮居中对齐，再夹进窗口（rail 左边那一格的中心几乎贴着屏幕左缘，居中
// 会把它推出画外）；纵向在哪由 `placeBubble` 定。
const bubbleStyle = computed(() => {
  const p = spot.value
  if (!p) return null
  return p.above
    ? { left: `${p.left}px`, top: `${p.top}px`, transform: 'translateY(-100%)' }
    : { left: `${p.left}px`, top: `${p.top}px` }
})
</script>

<template>
  <!-- 挂到 <body> 上，而不是留在挂在调用处的那棵子树里：换页时 `.app-content` 会演
       一个带 `translateX` 的动画，而 fixed 的定位基准会被祖先的 transform 换掉
       ——留在原地的话，气泡会跟着页面横着挪一下。 -->
  <Teleport to="body">
    <!-- `:key="step"`：换下一步时整块重画一次，淡入的那个动效才是「新的东西来了」，
         而不是两块东西在屏幕上滑来滑去。 -->
    <section v-if="box && ringStyle" :key="step" class="sg" :aria-label="t('work.startGuide.aria')">
      <div class="sg__ring" :style="ringStyle" aria-hidden="true" />
      <div v-if="bubbleStyle" :ref="holdBubble" class="sg__bubble" :style="bubbleStyle">
        <p class="sg__text" role="status">{{ t(COPY[step], { agent: agent ?? '' }) }}</p>
        <button type="button" class="sg__skip" @click="emit('skip')">
          {{ t('work.startGuide.skip') }}
        </button>
      </div>
    </section>
  </Teleport>
</template>

<style scoped>
/* 整层不吃指针。它只画一圈高亮和一条气泡，手指和小箭头要照常落到下面那颗按钮上。 */
.sg {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  pointer-events: none;
}

.sg__ring {
  position: fixed;
  border-radius: var(--radius-lg);
  /* 一圈实线加一圈淡光：实线是说「就是这颗」，淡光把视线从满屏内容里收过去。 */
  box-shadow:
    0 0 0 2px var(--accent),
    0 0 0 8px color-mix(in srgb, var(--accent) 18%, transparent);
  animation: sg-in var(--dur-base) var(--ease-out);
}

.sg__bubble {
  position: fixed;
  box-sizing: border-box;
  width: 240px;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
  /* 这一块要能点：它身上那颗「跳过引导」。 */
  pointer-events: auto;
  animation: sg-in var(--dur-base) var(--ease-out);
}

.sg__text {
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}

.sg__skip {
  display: block;
  margin: 6px 0 0 auto;
  padding: 2px 4px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
}
.sg__skip:hover {
  color: var(--muted);
}

@keyframes sg-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>
