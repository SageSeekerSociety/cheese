<script setup lang="ts">
// 工作面板的页签条。它从 `WorkPanel` 里搬出来，是因为它有两处用途：产品的话题页
// （`WorkPanel`）和文档里的动态演示（`views/demo/DemoRoom.vue`）。演示要的是同一
// 条栏——同样四格、同样的小点、同样的下边线——不然「产品一改、演示跟着变」这句话
// 在演示的第一行就不成立了。
//
// 它只管画和量，不管选哪一格：选中态、哪几格有东西、信号是什么，都是调用方的。
// 页签栏是纯展示的，所以搬出这一格不会把话题页的接线也搬走。
//
// 两段：固定区（调用方给的几格，不能关）和自由区（读者自己打开的那几份文件，
// 可以关——变化是他自己做的，所以不算「页签自己出现和消失」）。
import type { OpenFileTab } from '../../composables/useTopicMemory'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileIcon } from '../../lib/fileKind'
import { vRovingTabs } from '../../lib/rovingTabs'

import { t } from '@/i18n'

export interface PanelTab {
  key: string
  label: string
  icon: string
  /** 这一格此刻没东西：字退到 --faint，但照样能点。 */
  empty?: boolean
  /** 这一格自己的说法（hover / 读屏）。不写就是 label。 */
  title?: string
  /** 挂在页签上的信号。琥珀只给「有东西等你看」，绿点只给「正在发生」。 */
  signal?: { kind: 'pulse' | 'dot' | 'count'; count?: number; fresh?: boolean }
}

const props = withDefaults(
  defineProps<{
    tabs: PanelTab[]
    active: string
    /** 自由区那一份文件一个页签，键是 `file:<路径>`（见 `WorkPanel` 的 fileKey）。 */
    files?: OpenFileTab[]
    /** 窄屏（手机上这一格是「对话」时）的加高与横滚。 */
    phone?: boolean
    /** 页签切换的那块内容区（`role="tabpanel"`）的 id，给每一格的 `aria-controls`。 */
    panelId?: string
  }>(),
  { files: () => [], phone: false, panelId: undefined }
)

const emit = defineEmits<{
  (e: 'select', key: string): void
  (e: 'close-file', path: string): void
  (e: 'pin-file', path: string): void
}>()

function fileKey(path: string): string {
  return `file:${path}`
}
function fileName(path: string): string {
  return path.split('/').pop() || path
}

// 窄屏上这条栏会横向滚动，所以「哪一格是选中的」和「你看得见哪一格」不再是同一
// 件事：阶段自动选中的那一格（比如开工时的现场）可能整个在屏幕外，屏幕上什么都
// 没发生。选中态一变就把它带回视野里。
const tabbarRef = ref<HTMLElement | null>(null)

// 窄屏上这条栏横向滚动（见下面 .tabbar 的 overflow-x）：最后一个页签可以整个在屏幕
// 外，而滚动本身没有可见的把手，人不知道右边还有。量出这一侧到底有没有溢出，靠边的
// 淡出和一颗箭头因此只在真溢出时出现——和搜索页那条带 › 的栏目同一个意思（§8）。
const canLeft = ref(false)
const canRight = ref(false)
function measureOverflow() {
  const el = tabbarRef.value
  if (!el) return
  const max = el.scrollWidth - el.clientWidth
  canLeft.value = el.scrollLeft > 1
  canRight.value = el.scrollLeft < max - 1
}
// 一次翻大半屏：翻一格的话，最后那几格要点很多下才到得了。
function scrollTabs(dir: 1 | -1) {
  const el = tabbarRef.value
  if (!el) return
  el.scrollBy({ left: dir * Math.max(120, el.clientWidth * 0.8), behavior: 'smooth' })
}
watch(
  () => props.active,
  () => {
    void nextTick(() => {
      const on = tabbarRef.value?.querySelector('[aria-selected="true"]')
      on?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      measureOverflow()
    })
  }
)

// 选中那一格下面的线是一条，换页签时从旧的那一格滑到新的那一格（§9.2：位置变了，
// 就让人看见它是从哪儿挪过来的）。每一格各画一条的话，换页签是一条消失、另一条
// 凭空出现，读不出「从这儿到那儿」。
//
// 量的是选中那一格自己的盒子，所以一格的宽度变了（计数出现、字体加载完）也得重量
// 一次——盯着的就是那一格。第一次落位不演：打开房间时线本来就在那儿。
const ink = ref<{ left: number; width: number } | null>(null)
const inkMoves = ref(false)
let inkWatch: ResizeObserver | null = null
let inkTarget: Element | null = null
function placeInk() {
  const on = tabbarRef.value?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
  if (!on) {
    ink.value = null
    return
  }
  // 页签在 `.tabbar__file` 里的时候 offsetLeft 量的也是到 `.tabbar` 的距离：那层
  // 包装没有定位，偏移的基准一路落到定了位的 `.tabbar` 上。
  ink.value = { left: on.offsetLeft, width: on.offsetWidth }
  // 只在选中的换了一格时改盯的对象：`observe` 一挂上就先回调一次，回调里再
  // `disconnect` + `observe` 同一格，就是每一帧都在重挂、每一帧都在报 ResizeObserver
  // 循环。
  if (on !== inkTarget && typeof ResizeObserver !== 'undefined') {
    inkWatch?.disconnect()
    inkWatch ??= new ResizeObserver(() => placeInk())
    inkWatch.observe(on)
    inkTarget = on
  }
  if (!inkMoves.value) requestAnimationFrame(() => (inkMoves.value = true))
}
watch(
  [() => props.active, () => props.files.length, tabbarRef],
  () =>
    void nextTick(() => {
      placeInk()
      measureOverflow()
    }),
  { immediate: true }
)
// 字号加载完、多一格自由区页签、容器换宽都会改溢出，光靠 scroll 事件量不到。
let overflowWatch: ResizeObserver | null = null
onMounted(() => {
  measureOverflow()
  if (typeof ResizeObserver !== 'undefined' && tabbarRef.value) {
    overflowWatch = new ResizeObserver(() => measureOverflow())
    overflowWatch.observe(tabbarRef.value)
  }
})
onBeforeUnmount(() => {
  inkWatch?.disconnect()
  overflowWatch?.disconnect()
})
const inkStyle = computed(() =>
  ink.value
    ? { transform: `translateX(${ink.value.left + 8}px)`, width: `${Math.max(0, ink.value.width - 16)}px` }
    : { display: 'none' }
)
</script>

<template>
  <div class="tabbar-host">
    <div
      ref="tabbarRef"
      v-roving-tabs
      class="tabbar"
      :class="{ 'tabbar--phone': phone }"
      role="tablist"
      @scroll="measureOverflow"
    >
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="tabbar__tab"
        :class="{ 'tabbar__tab--on': active === tab.key, 'tabbar__tab--empty': tab.empty }"
        :aria-selected="active === tab.key"
        :aria-controls="panelId"
        :title="tab.title ?? tab.label"
        @click="emit('select', tab.key)"
      >
        <v-icon size="16">{{ tab.icon }}</v-icon>
        {{ tab.label }}
        <!-- 信号上 Tab，不抢占视图: 芝士 works for minutes at a time and the
           reader is usually somewhere else while it does, so what it produced
           has to be visible from the tab it produced it on. None of these ever
           selects a tab for you. -->
        <span v-if="tab.signal?.kind === 'pulse'" class="tabbar__pulse" />
        <!-- A dot, not a count: there is only ever one current preview, so a
           number would be noise. -->
        <span v-else-if="tab.signal?.kind === 'dot'" class="tabbar__dot" />
        <!-- 有几件在路上。和 改动 一样用数字而不是点：几件在跑本身就是要看的那个
           信息。它不变色——派出去的活不是「你还没看过的东西」。 -->
        <span
          v-else-if="tab.signal?.kind === 'count' && tab.signal.count"
          class="tabbar__count"
          :class="{ 'tabbar__count--new': tab.signal.fresh }"
          >{{ tab.signal.count }}</span
        >
      </button>
      <!-- 自由区。关闭钮和页签是兄弟，不是它的孩子：按钮里套按钮不合法，读屏也会把
         两者念成一个东西。 -->
      <span v-if="files.length" class="tabbar__sep" aria-hidden="true" />
      <div v-for="f in files" :key="fileKey(f.path)" class="tabbar__file" :class="{ 'tabbar__file--temp': !f.pinned }">
        <button
          type="button"
          role="tab"
          class="tabbar__tab"
          :class="{ 'tabbar__tab--on': active === fileKey(f.path) }"
          :aria-selected="active === fileKey(f.path)"
          :aria-controls="panelId"
          :title="f.pinned ? f.path : t('work.room.tabs.pinHint', { path: f.path })"
          @click="emit('select', fileKey(f.path))"
          @dblclick="emit('pin-file', f.path)"
        >
          <v-icon size="16">{{ fileIcon(f.path) }}</v-icon>
          <span class="tabbar__name">{{ fileName(f.path) }}</span>
        </button>
        <button
          type="button"
          class="tabbar__close"
          :aria-label="t('work.room.tabs.close', { name: fileName(f.path) })"
          :title="t('work.room.tabs.close', { name: fileName(f.path) })"
          @click="emit('close-file', f.path)"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </div>
      <span class="tabbar__ink" :class="{ 'tabbar__ink--moves': inkMoves }" :style="inkStyle" aria-hidden="true" />
    </div>
    <!-- 溢出到屏幕外的那一侧：一层淡出 + 一颗箭头。它们只在那一侧真溢出时出现，
         滚到头就退回去，不挡着最后一格。箭头读屏念得出，手指点得中（44×44）。 -->
    <div v-if="canLeft" class="tabbar-host__edge tabbar-host__edge--left" aria-hidden="true" />
    <div v-if="canRight" class="tabbar-host__edge tabbar-host__edge--right" aria-hidden="true" />
    <button
      v-if="canLeft"
      type="button"
      class="tabbar-host__arrow tabbar-host__arrow--left"
      :aria-label="t('work.room.tabs.scrollLeft')"
      :title="t('work.room.tabs.scrollLeft')"
      @click="scrollTabs(-1)"
    >
      <v-icon size="20">mdi-chevron-left</v-icon>
    </button>
    <button
      v-if="canRight"
      type="button"
      class="tabbar-host__arrow tabbar-host__arrow--right"
      :aria-label="t('work.room.tabs.scrollRight')"
      :title="t('work.room.tabs.scrollRight')"
      @click="scrollTabs(1)"
    >
      <v-icon size="20">mdi-chevron-right</v-icon>
    </button>
  </div>
</template>

<style scoped>
.tabbar {
  display: flex;
  position: relative;
  padding: 0 6px;

  /* 一屏放不下的时候横着滚，而不是把每一格压扁：挤压是没有边界的——tab 只会越
     加越多，而窄屏上第一个被挤没的永远是文字，剩下一排认不出来的图标。滚动条不
     画出来，因为这条栏本来就只有一行高，一条滚动条会占掉它三分之一。 */
  overflow-x: auto;
  flex: 0 0 auto;
  align-items: stretch;
  gap: 2px;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}

.tabbar::-webkit-scrollbar {
  display: none;
}

.tabbar__tab {
  display: inline-flex;
  position: relative;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: none;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
}

.tabbar__tab:hover {
  color: var(--ink);
}

/* 手机上一格页签至少 44px 高，手指点得中。栏会横向滚动，撑开的伪元素会被裁掉，
   所以是真的长高。 */
.tabbar--phone .tabbar__tab {
  min-height: 44px;
}

/* 这一格此刻没东西：字退到 --faint，但照样能点，点进去是它自己的「暂无」。 */
.tabbar__tab--empty:not(.tabbar__tab--on) {
  color: var(--faint);
}

/* 固定区和自由区之间的那一道：前面几格永远在，后面几格是你自己开的。 */
.tabbar__sep {
  flex: 0 0 auto;
  align-self: center;
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--line);
}

.tabbar__file {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
}

.tabbar__file .tabbar__tab {
  padding-right: 4px;
}

.tabbar__name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 临时位：下一次打开会换掉它。斜体是编辑器里通行的说法；双击就不斜了。 */
.tabbar__file--temp .tabbar__name {
  font-style: italic;
}

.tabbar__close {
  display: inline-flex;
  width: 20px;
  height: 20px;
  padding: 0;
  margin-right: 4px;
  color: var(--faint);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  align-items: center;
  justify-content: center;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.tabbar__close:hover {
  color: var(--ink);
  background: var(--fill);
}

/* 选中态: ink + 一条下边线。琥珀只留给唯一主操作、导航选中态和品牌标，工作面板的
   tab 不是导航，所以用中性墨色。 */
.tabbar__tab--on {
  font-weight: 600;
  color: var(--ink);
}

.tabbar__ink {
  position: absolute;
  bottom: -1px;
  left: 0;
  height: 2px;
  pointer-events: none;
  background: var(--ink);
}

.tabbar__ink--moves {
  transition:
    transform var(--dur-base) var(--ease-standard),
    width var(--dur-base) var(--ease-standard);
}

/* 有新内容 —— 琥珀在这条 tab 栏里只给「有东西等你看」，不给选中态。 */
.tabbar__dot {
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
}

/* 芝士正在这个 tab 后面干活。呼吸而不是常亮：常亮说的是「有个东西」，呼吸说的
   是「正在发生」，而现场这一片的全部意义就是后者。 */
.tabbar__pulse {
  width: 6px;
  height: 6px;
  background: var(--ok);
  border-radius: 50%;
  animation: tabbar-breathe 1.6s ease-in-out infinite;
}

@keyframes tabbar-breathe {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.3;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tabbar__pulse {
    animation: none;
  }
}

/* 改动的规模。默认是中性的事实，只有「你还没看过那些」才配琥珀。 */
.tabbar__count {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}

.tabbar__count--new {
  color: var(--accent);
}

/* 窄屏上这条栏横向滚动（见 .tabbar 的 overflow-x）：最后一个页签可以整个在屏幕外，
   而滚动本身没有把手，人不知道右边还有。和搜索页那条带 › 的栏目一样，溢出时靠边的
   淡出和一颗箭头把「还有」说出来；哪一侧有溢出哪一侧才出现，滚到头就退回去。 */
.tabbar-host {
  display: flex;
  position: relative;
  min-width: 0;
  flex: 0 0 auto;
}

.tabbar-host .tabbar {
  flex: 1 1 auto;
  min-width: 0;
}

.tabbar-host__edge {
  position: absolute;
  top: 0;
  bottom: 1px;
  z-index: 1;
  width: 40px;
  pointer-events: none;
}

.tabbar-host__edge--right {
  right: 0;
  background: linear-gradient(to right, transparent, var(--surface));
}

.tabbar-host__edge--left {
  left: 0;
  background: linear-gradient(to left, transparent, var(--surface));
}

/* 箭头本身 44×44（手指点得中的下限），记号画小一号。它压在栏的边上，所以只在那一侧
   有溢出的那几秒里出现——滚到头它就不在，不挡着最后一格。 */
.tabbar-host__arrow {
  display: inline-flex;
  position: absolute;
  top: 50%;
  z-index: 2;
  width: 44px;
  height: 44px;
  padding: 0;
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: var(--radius-pill);
  transform: translateY(-50%);
  align-items: center;
  justify-content: center;
  transition: color var(--dur-quick) var(--ease-standard);
}

.tabbar-host__arrow--right {
  right: 0;
}

.tabbar-host__arrow--left {
  left: 0;
}

.tabbar-host__arrow:hover {
  color: var(--ink);
}
</style>
