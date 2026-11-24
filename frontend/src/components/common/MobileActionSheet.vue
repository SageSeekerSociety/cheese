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
//
// 一项带 children 的是下一层：点它不关面板，面板换成那一层，顶上一颗返回回到上一层。
// 面板很窄，旁边弹不出子菜单，所以就地换。
import type { MenuAction } from './menuAction'

import { computed, ref } from 'vue'

import { useFocusReturn } from '@/composables/useFocusReturn'
import { useNavigation } from '@/composables/useNavigation'

import { t } from '@/i18n'

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

/** 点开的那一层；null 是最上面那一层。 */
const level = ref<MenuAction | null>(null)
const shownActions = computed(() => level.value?.children ?? props.actions)

function choose(action: MenuAction) {
  if (action.disabled || action.loading) return
  if (action.children) {
    level.value = action
    return
  }
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
  level.value = null
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
      <div v-if="level" class="action-sheet__header action-sheet__header--level">
        <button type="button" class="action-sheet__back" :aria-label="t('global.back')" @click="level = null">
          <v-icon size="20" icon="mdi-chevron-left" />
        </button>
        <span class="t-meta">{{ level.label }}</span>
      </div>
      <div v-else-if="$slots.header || props.title" class="action-sheet__header">
        <slot name="header">
          <span class="t-meta">{{ props.title }}</span>
        </slot>
      </div>
      <slot v-if="!level" />
      <ul v-if="shownActions.length" class="action-sheet__list" role="menu">
        <li
          v-for="action in shownActions"
          :key="action.key"
          role="none"
          :class="{ 'action-sheet__row--apart': action.divider }"
        >
          <div v-if="action.section" class="action-sheet__section t-meta">{{ action.section }}</div>
          <button
            type="button"
            :role="action.checked === undefined ? 'menuitem' : 'menuitemradio'"
            :aria-checked="action.checked === undefined ? undefined : action.checked ? 'true' : 'false'"
            :aria-haspopup="action.children ? 'menu' : undefined"
            class="action-sheet__item"
            :class="{ 'action-sheet__item--danger': action.danger }"
            :disabled="action.disabled || action.loading"
            :aria-busy="action.loading || undefined"
            @click="choose(action)"
          >
            <v-progress-circular v-if="action.loading" indeterminate size="18" width="2" />
            <v-icon v-else-if="action.checked !== undefined" size="20" aria-hidden="true">{{
              action.checked ? 'mdi-check' : ''
            }}</v-icon>
            <v-icon v-else size="20" aria-hidden="true">{{ action.icon }}</v-icon>
            <span class="action-sheet__label">
              <span class="action-sheet__name">{{ action.label }}</span>
              <span v-if="action.hint" class="action-sheet__hint">{{ action.hint }}</span>
            </span>
            <span v-if="action.value" class="action-sheet__value">{{ action.value }}</span>
            <v-icon v-if="action.children" size="18" class="action-sheet__chevron" icon="mdi-chevron-right" />
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
/* 上面几件和「不可撤销的那一件」分开：桌面上那里是一条分隔线，这里是面板，线跟着
   面板的宽度走。 */
.action-sheet__row--apart {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--line);
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
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}
.action-sheet__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 名字下面那一行小字（通知档位的说明）：可以折行，比名字弱一档。 */
.action-sheet__hint {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.action-sheet__item:has(.action-sheet__hint) {
  padding-block: 8px;
}
.action-sheet__value {
  flex: none;
  max-width: 45%;
  overflow: hidden;
  color: var(--muted);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.action-sheet__chevron {
  flex: none;
}
/* 一组的小标题（「静音一段时间」）。 */
.action-sheet__section {
  padding: 8px 20px 4px;
  color: var(--muted);
}
.action-sheet__header--level {
  display: flex;
  align-items: center;
  gap: 4px;
  padding-inline-start: 8px;
}
.action-sheet__back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: var(--radius-md);
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
