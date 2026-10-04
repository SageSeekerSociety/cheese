<script setup lang="ts">
// 底部动作面板：手机上一组操作从屏幕底部升起来。
//
//   <MobileActionSheet v-model="open" :actions="actions" title="话题">
//     <template #header>……一排表情……</template>
//     ……任意内容，画在操作列表上面……
//   </MobileActionSheet>
//
// 点遮罩、按 Esc、往下拖都会关上（前两样是 v-bottom-sheet 自己的）。选中一项先关上
// 面板，再跑它的 onSelect / 去它的 to。升起用 --dur-base / --ease-standard，收起快一
// 档；减弱动效时直接出现。底部让出 safe-area-inset-bottom。
//
// 要「桌面是菜单、手机是面板」的，用 AdaptiveMenu，别在页面里自己分两支。
import type { MenuAction } from './menuAction'

import { computed, ref } from 'vue'

import { useFocusReturn } from '@/composables/useFocusReturn'
import { useNavigation } from '@/composables/useNavigation'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    actions?: MenuAction[]
    /** 面板顶上的一行小标题；要更多东西就用 #header。 */
    title?: string
  }>(),
  { actions: () => [], title: undefined }
)

const emit = defineEmits<{ select: [action: MenuAction] }>()

defineSlots<{
  header?: () => unknown
  default?: () => unknown
}>()

const nav = useNavigation()

function choose(action: MenuAction) {
  if (action.disabled || action.loading) return
  open.value = false
  emit('select', action)
  action.onSelect?.()
  if (action.to) nav?.navigate(action.to)
}

// ---- 往下拖着关 ------------------------------------------------------------
//
// 只在面板里能滚的那一块已经滚到顶、并且手指往下走的时候才算拖面板；否则是正常
// 滚动。拖过面板高度的三成（最多 120px）或者甩得够快就关上，不然弹回原位。
const DRAG_START = 6
const panel = ref<HTMLElement | null>(null)
const offset = ref(0)
const settling = ref(false)
let startY = 0
let startAt = 0
let atTop = true
let dragging = false

function scrolledToTop(target: EventTarget | null): boolean {
  for (let el = target as HTMLElement | null; el && el !== panel.value; el = el.parentElement) {
    if (el.scrollHeight > el.clientHeight && el.scrollTop > 0) return false
  }
  return (panel.value?.scrollTop ?? 0) <= 0
}

function onTouchStart(e: TouchEvent) {
  if (e.touches.length !== 1) return
  startY = e.touches[0].clientY
  startAt = e.timeStamp
  atTop = scrolledToTop(e.target)
  dragging = false
  settling.value = false
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length !== 1) return
  const dy = e.touches[0].clientY - startY
  if (!dragging && atTop && dy > DRAG_START) dragging = true
  if (!dragging) return
  e.preventDefault()
  offset.value = Math.max(0, dy - DRAG_START)
}

function onTouchEnd(e: TouchEvent) {
  if (!dragging) return
  dragging = false
  const height = panel.value?.offsetHeight ?? 0
  const speed = offset.value / Math.max(1, e.timeStamp - startAt)
  if (offset.value > Math.min(120, height * 0.3) || speed > 0.6) {
    open.value = false
    return
  }
  settling.value = true
  offset.value = 0
}

// 关上之后把位移清掉：下一次从底部完整地升起来。
function reset() {
  offset.value = 0
  settling.value = false
}

const panelStyle = computed(() => (offset.value ? { transform: `translateY(${offset.value}px)` } : undefined))

// 关掉时把焦点还回打开它的那一处：v-bottom-sheet 没有 activator，Vuetify 自己不管。
useFocusReturn(open)
</script>

<template>
  <v-bottom-sheet
    v-model="open"
    class="action-sheet"
    content-class="action-sheet__content"
    transition="action-sheet"
    @after-leave="reset"
  >
    <div
      ref="panel"
      class="action-sheet__panel"
      :class="{ 'action-sheet__panel--settling': settling }"
      :style="panelStyle"
      @touchstart="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="onTouchEnd"
      @touchcancel="onTouchEnd"
    >
      <div class="action-sheet__handle" aria-hidden="true" />
      <div v-if="$slots.header || props.title" class="action-sheet__header">
        <slot name="header">
          <span class="t-meta">{{ props.title }}</span>
        </slot>
      </div>
      <slot />
      <ul v-if="props.actions.length" class="action-sheet__list" role="menu">
        <li v-for="action in props.actions" :key="action.key" role="none">
          <button
            type="button"
            role="menuitem"
            class="action-sheet__item"
            :class="{ 'action-sheet__item--danger': action.danger }"
            :disabled="action.disabled || action.loading"
            :aria-busy="action.loading || undefined"
            @click="choose(action)"
          >
            <v-progress-circular v-if="action.loading" indeterminate size="18" width="2" />
            <v-icon v-else size="20" aria-hidden="true">{{ action.icon }}</v-icon>
            <span class="action-sheet__label">{{ action.label }}</span>
            <span v-if="action.badge" class="action-sheet__badge">{{ action.badge }}</span>
          </button>
        </li>
      </ul>
    </div>
  </v-bottom-sheet>
</template>

<style scoped>
.action-sheet__panel {
  display: flex;
  flex-direction: column;
  max-height: calc(var(--app-height, 100dvh) - var(--keyboard-inset, 0px) - 48px);
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
  overflow-y: auto;
  overscroll-behavior: contain;
  background: var(--surface);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.action-sheet__panel--settling {
  transition: transform var(--dur-base) var(--ease-standard);
}
/* 拖动的把手：告诉人这块可以往下拖。 */
.action-sheet__handle {
  flex: none;
  width: 36px;
  height: 4px;
  margin: 8px auto 4px;
  background: var(--line-2);
  border-radius: var(--radius-pill);
}
.action-sheet__header {
  flex: none;
  padding: 8px 16px;
  color: var(--muted);
}
.action-sheet__list {
  margin: 0;
  padding: 4px 0;
  list-style: none;
}
.action-sheet__item {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-height: 48px;
  padding: 0 20px;
  color: var(--text);
  font-size: 15px;
  line-height: var(--lh-15);
  text-align: start;
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.action-sheet__item .v-icon {
  color: var(--muted);
}
.action-sheet__item:active:not(:disabled) {
  background: var(--fill);
}
.action-sheet__item:disabled {
  color: var(--faint);
  cursor: default;
}
.action-sheet__item:disabled .v-icon {
  color: var(--faint);
}
.action-sheet__item--danger,
.action-sheet__item--danger .v-icon {
  color: var(--danger-ink);
}
.action-sheet__label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 未读数：裸的琥珀数字，和侧栏行尾那一个同一种画法。 */
.action-sheet__badge {
  flex: none;
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
</style>

<style>
/* 面板本身由 v-bottom-sheet 传送到 body 下的浮层里，这几条碰的是 Vuetify 的元素，
   写不进 scoped。 */
.v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet__content {
  max-width: 640px;
  box-shadow: none;
}
/* 选择器写到四级是为了压过 VBottomSheet.css 里那条三级的 transition-duration: .2s。 */
.v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-enter-active {
  transition: transform var(--dur-base) var(--ease-standard);
}
.v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-leave-active {
  transition: transform var(--dur-quick) var(--ease-in);
}
.v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-enter-from,
.v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-leave-to {
  transform: translateY(100%);
}
@media (prefers-reduced-motion: reduce) {
  .v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-enter-active,
  .v-bottom-sheet.action-sheet > .v-overlay__content.action-sheet-leave-active {
    transition: none;
  }
}
</style>
