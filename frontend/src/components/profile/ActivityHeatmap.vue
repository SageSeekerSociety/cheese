<script setup lang="ts">
// 一年的活动图：一列一周、一行一个星期几，深浅只用中性墨色（§1.6 琥珀不进图、
// §1.7 不给第二个颜色族）。
//
// 每一格是一颗按钮，读屏念出日期和条数；点一格选中它所在的那一周，页面按这一周
// 去筛「最近参与的话题」。整张图只占一个 Tab 停靠点：方向键在格子之间走（行 =
// 星期几，列 = 周），不必把 365 格一格一格 Tab 过去。
//
// 表格孪生体（§1.7）：下面那个默认收起的「查看数据表」按周列出条数——抄一个数、
// 用读屏整体浏览的人从那里拿。按天的数在每一格自己的名字里。
//
// `compact` 是手机上的那一张：半年、格子太小点不准，所以只看不点，也不画月份和图例。
import type { ProfileActivityDay } from '@/cx_types'
import type { ActivityWeek } from '@/lib/activityYear'

import { computed, nextTick, ref } from 'vue'

import i18n, { t } from '@/i18n'
import { activityWeeks, formatUtcDay, monthStarts } from '@/lib/activityYear'

const props = defineProps<{
  days: ProfileActivityDay[]
  /** 选中那一周的星期一；没有选中是 null。 */
  selected: string | null
  compact?: boolean
}>()

const emit = defineEmits<{ select: [week: ActivityWeek] }>()

const HALF_YEAR_WEEKS = 26

const weeks = computed(() => activityWeeks(props.days, props.compact ? HALF_YEAR_WEEKS : undefined))
const locale = computed(() => i18n.global.locale.value)

const months = computed(() => {
  const marks = monthStarts(weeks.value)
  return marks.map((mark, i) => ({
    ...mark,
    span: (marks[i + 1]?.column ?? weeks.value.length) - mark.column,
    label: new Intl.DateTimeFormat(locale.value, { month: 'short', timeZone: 'UTC' }).format(
      Date.UTC(2000, mark.month - 1, 1)
    ),
  }))
})

function dayLabel(date: string): string {
  return formatUtcDay(date, locale.value, { month: 'long', day: 'numeric' })
}

function weekLabel(week: ActivityWeek): string {
  return t('users.profile.topics.range', { from: dayLabel(week.from), to: dayLabel(week.to) })
}

// ---- 方向键 ----
// 能拿到焦点的只有一格（roving tabindex）：选中的那一周里今天之前最后一个有数的
// 格子，没有选中就是最新的那一天。
const focusAt = ref<string | null>(null)
const lastDate = computed(() => props.days[props.days.length - 1]?.date ?? null)
const tabStop = computed(() => {
  if (focusAt.value) return focusAt.value
  if (props.selected) {
    const week = weeks.value.find((w) => w.from === props.selected)
    const cells = week?.cells.filter((c) => c !== null) ?? []
    if (cells.length) return cells[cells.length - 1]!.date
  }
  return lastDate.value
})

const grid = ref<HTMLElement | null>(null)

async function move(event: KeyboardEvent, column: number, row: number) {
  const step: Record<string, [number, number]> = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  }
  let target: { column: number; row: number } | null = null
  if (event.key in step) {
    const [dc, dr] = step[event.key]!
    target = { column: column + dc, row: row + dr }
  } else if (event.key === 'Home') target = { column: 0, row }
  else if (event.key === 'End') target = { column: weeks.value.length - 1, row }
  if (!target) return
  event.preventDefault()
  const cell = weeks.value[target.column]?.cells[target.row]
  if (!cell) return
  focusAt.value = cell.date
  await nextTick()
  grid.value?.querySelector<HTMLButtonElement>(`[data-date="${cell.date}"]`)?.focus()
}
</script>

<template>
  <div class="heatmap" :class="{ 'heatmap--compact': compact }">
    <div
      v-if="!compact"
      class="heatmap__months"
      :style="{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }"
      aria-hidden="true"
    >
      <span
        v-for="m in months"
        :key="m.column"
        class="heatmap__month"
        :style="{ gridColumn: `${m.column + 1} / span ${m.span}` }"
        >{{ m.label }}</span
      >
    </div>

    <div
      ref="grid"
      class="heatmap__weeks"
      :style="{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }"
      :role="compact ? undefined : 'group'"
      :aria-label="compact ? undefined : t('users.profile.activity.grid')"
      :aria-hidden="compact ? 'true' : undefined"
    >
      <div
        v-for="(week, column) in weeks"
        :key="week.from"
        class="heatmap__week"
        :class="{ 'heatmap__week--selected': !compact && week.from === selected }"
      >
        <template v-for="(cell, row) in week.cells" :key="row">
          <span v-if="!cell" class="heatmap__cell heatmap__cell--void" />
          <span v-else-if="compact" class="heatmap__cell" :class="`heatmap__cell--l${cell.level}`" />
          <button
            v-else
            type="button"
            class="heatmap__cell"
            :class="`heatmap__cell--l${cell.level}`"
            :data-date="cell.date"
            :tabindex="cell.date === tabStop ? 0 : -1"
            :aria-label="t('users.profile.activity.cell', { date: dayLabel(cell.date), count: cell.count })"
            :aria-pressed="week.from === selected"
            :title="t('users.profile.activity.cell', { date: dayLabel(cell.date), count: cell.count })"
            @click="emit('select', week)"
            @focus="focusAt = cell.date"
            @keydown="move($event, column, row)"
          />
        </template>
      </div>
    </div>

    <div v-if="!compact" class="heatmap__legend" aria-hidden="true">
      <span>{{ t('users.profile.activity.less') }}</span>
      <span v-for="level in [0, 1, 2, 3, 4]" :key="level" class="heatmap__swatch" :class="`heatmap__cell--l${level}`" />
      <span>{{ t('users.profile.activity.more') }}</span>
    </div>

    <details class="heatmap__twin">
      <summary>{{ t('users.profile.activity.table') }}</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">{{ t('users.profile.activity.colWeek') }}</th>
            <th scope="col">{{ t('users.profile.activity.colCount') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="week in [...weeks].reverse()" :key="week.from">
            <td>{{ weekLabel(week) }}</td>
            <td class="t-num">{{ week.total }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </div>
</template>

<style scoped>
.heatmap {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.heatmap__months {
  display: grid;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.heatmap__month {
  overflow: hidden;
  white-space: nowrap;
}
.heatmap__weeks {
  display: grid;
  gap: 2px;
}
.heatmap__week {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-radius: 0;
  outline: 1.5px solid transparent;
  outline-offset: 1px;
  transition: outline-color var(--dur-quick) var(--ease-standard);
}
.heatmap__week--selected {
  outline-color: var(--ink);
}
.heatmap__cell {
  display: block;
  width: 100%;
  aspect-ratio: 1;
  margin: 0;
  padding: 0;
  border: 0;
}
button.heatmap__cell {
  cursor: pointer;
  transition: box-shadow var(--dur-quick) var(--ease-standard);
}
button.heatmap__cell:hover {
  box-shadow: 0 0 0 1px var(--muted);
}
button.heatmap__cell:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.heatmap__cell--void {
  background: none;
}
/* 五档中性墨色：0 是底色一档，1–4 是 --ink 往 --surface 里兑的比例。两个主题下
   都是「越深越多」——深色主题里 --ink 是亮的，兑出来就是越亮越多。 */
.heatmap__cell--l0 {
  background: var(--fill-2);
}
.heatmap__cell--l1 {
  background: color-mix(in srgb, var(--ink) 16%, var(--surface));
}
.heatmap__cell--l2 {
  background: color-mix(in srgb, var(--ink) 34%, var(--surface));
}
.heatmap__cell--l3 {
  background: color-mix(in srgb, var(--ink) 56%, var(--surface));
}
.heatmap__cell--l4 {
  background: color-mix(in srgb, var(--ink) 82%, var(--surface));
}
.heatmap__legend {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.heatmap__swatch {
  width: 10px;
  height: 10px;
}
.heatmap__twin summary {
  width: fit-content;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
}
.heatmap__twin table {
  width: 100%;
  margin-top: 8px;
  border-collapse: collapse;
  font-size: 13px;
  line-height: var(--lh-13);
}
.heatmap__twin th {
  color: var(--muted);
  font-weight: 600;
  text-align: start;
}
.heatmap__twin th,
.heatmap__twin td {
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
}
.heatmap__twin th:last-child,
.heatmap__twin td:last-child {
  text-align: end;
}
</style>
