<script setup lang="ts">
// 面板中间那一列结果：分组、每行的图标和名字（对上的几个字高亮）、右侧的角标。
//
// 行上三种「还能做什么」的入口都从这里出去，做不做由父级判：Tab 和右键是父级键盘表里
// 的键，长按要挂在列表这一层自己身上（`useLongPress` 贴在元素上），所以它认得按的是
// 第几行，再把那一行报上去。
import type { ResultGroup, ResultRow } from './results'

import { computed, ref } from 'vue'

import { useLongPress } from '@/composables/useLongPress'

import { optionId, resultsId } from './ids'
import { shortcutLabel } from './keys'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 一组一组的结果，顺序就是屏幕上的顺序。 */
  groups: ResultGroup[]
  /** 高亮的是第几行（跨组数下来的）。 */
  selected: number
  /** 输入框里现在是什么：只有真打了字、又什么都没搜到时才提示。 */
  input: string
  /** 正在问远程数据源：这时候先别急着说「没有」。 */
  searching: boolean
  /** 手机上面板是一整页，行更高，列表也得给底部安全区让位。 */
  mdAndUp: boolean
}>()

const emit = defineEmits<{
  /** 选中一行（鼠标划过）：按是第几行报回去。 */
  select: [index: number]
  /** 打开一行。按住修饰键的那一下是在新标签页打开。 */
  choose: [row: ResultRow, newTab: boolean]
  /** 右键一行。 */
  contextmenu: [event: MouseEvent, index: number]
  /** 长按一行。 */
  longpress: [index: number]
  /** 点了列表的任意一处：桌面上这会收起开着的那份清单。 */
  dismiss: []
}>()

// 每一组的第一行跨组数下来是第几行。行号是「按 Tab、按方向键」这一层的心跳：输入框的
// aria-activedescendant 指的就是这套号码，所以它得跟着结果一起动。
const offsets = computed(() => {
  let at = 0
  return props.groups.map((group) => {
    const start = at
    at += group.rows.length
    return start
  })
})

const list = ref<HTMLElement | null>(null)
useLongPress(list, (event) => {
  const index = Number((event.target as HTMLElement | null)?.closest?.('[data-row]')?.getAttribute('data-row'))
  emit('longpress', index)
})

/** 名字里对上输入的那一段，切成高亮和不亮的两三块。 */
function segments(row: ResultRow): { text: string; hit: boolean }[] {
  if (!row.range) return [{ text: row.title, hit: false }]
  const [from, to] = row.range
  return [
    { text: row.title.slice(0, from), hit: false },
    { text: row.title.slice(from, to), hit: true },
    { text: row.title.slice(to), hit: false },
  ].filter((part) => part.text)
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/interactive-supports-focus -- focus stays in the palette's input, which owns the keyboard for this listbox through aria-activedescendant -->
  <div
    :id="resultsId"
    ref="list"
    class="palette__list"
    :class="{ 'palette__list--page': !mdAndUp }"
    role="listbox"
    :aria-label="t('navigation.palette.open')"
    @click.capture="emit('dismiss')"
  >
    <template v-for="(group, g) in groups" :key="group.key">
      <div v-if="group.label" class="palette__group t-eyebrow-read" role="presentation">
        {{ t(group.label) }}
      </div>
      <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/interactive-supports-focus -- an option of the listbox above; the input picks it with the arrow keys and Enter -->
      <div
        v-for="(row, i) in group.rows"
        :id="optionId(offsets[g] + i)"
        :key="`${group.key}:${row.id}`"
        class="palette__row"
        :class="{ 'palette__row--selected': offsets[g] + i === selected }"
        role="option"
        :aria-selected="offsets[g] + i === selected"
        :data-row="offsets[g] + i"
        :title="shortcutLabel(row.shortcut)"
        @mousemove="emit('select', offsets[g] + i)"
        @click="emit('choose', row, $event.metaKey || $event.ctrlKey)"
        @contextmenu="emit('contextmenu', $event, offsets[g] + i)"
      >
        <v-icon :icon="row.icon" size="18" class="palette__icon" />
        <span class="palette__text">
          <span class="palette__title">
            <template v-for="(part, p) in segments(row)" :key="p">
              <mark v-if="part.hit" class="palette__hit">{{ part.text }}</mark>
              <template v-else>{{ part.text }}</template>
            </template>
          </span>
          <span v-if="row.subtitle" class="palette__subtitle">{{ row.subtitle }}</span>
        </span>
        <span v-if="row.badge" class="palette__badge" :class="row.badge.tone && `palette__badge--${row.badge.tone}`">
          {{ row.badge.text }}
        </span>
      </div>
    </template>
    <BaseEmptyState
      v-if="input.trim() && input.trim() !== '?' && !groups.length && !searching"
      size="inline"
      align="center"
      class="palette__empty"
      :title="t('navigation.palette.empty')"
    />
  </div>
</template>

<style scoped>
.palette__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 6px 8px;
}
.palette__group {
  padding: 10px 10px 4px;
}
.palette__row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: var(--text);
  cursor: pointer;
}
.palette__row--selected {
  background: var(--fill-2);
  color: var(--ink);
}
.palette__icon {
  flex: none;
  color: var(--muted);
}
.palette__text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}
.palette__title,
.palette__subtitle {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.palette__subtitle {
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}
.palette__hit {
  background: none;
  color: var(--accent-ink);
  font-weight: 600;
}
.palette__badge {
  flex: none;
  padding: 0 8px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.palette__badge--warn {
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.palette__badge--ok {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.palette__empty {
  padding: 24px 10px;
}

/* 手机上行更高。 */
.palette__list--page .palette__row {
  min-height: 48px;
}
/* 手机上这一页自己铺到底：最后几行结果落在 iPhone 那条 Home 横杠上点不中，给列表
   末尾让出安全区，叠在 `.palette__list` 本来的 8px 底距上（`padding: 4px 6px 8px`）。
   只写 `env()` 会把那 8px 一起抹掉——没有安全区的设备（安卓等）上 env 是 0，最后
   一行就会贴着屏幕底。桌面同理，长度不变。 */
.palette__list--page {
  padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
}
</style>
