<script setup lang="ts">
// 一条规则。项目总览页和房间右侧那一格画的是同一条：名字、什么时候跑、此刻它怎么了，
// 以及此刻你能对它做什么。
//
// 行上只有三样：名字、什么时候跑、一颗状态图形。**状态不写字**，四档各是一个图形
// （`routineStateIcon`）加一个颜色，读屏靠 `aria-label` 说那句话。动手的那几件全收进
// 行尾的 ⋯ —— 桌面上不再是一排文字按钮：一行的块头要留给「这条现在是活的吗」。
//
// 只吃 props（`Routine` 的形状在 `lib/routine.ts`：画的那一半碰不得 API 层），动作一律
// 往上抛 —— 「谁去调接口」是页面的事。**删得掉删不掉由这条规则自己说**：`can_delete` 是
// 后端逐条算好的（规则主人或项目管理员），前端不猜第二遍；其余的动手都不再问一次权限，
// 读得到就改得动。
//
// 展开的记录只画最近三次（看全部去那一整页）：想看一眼刚才跑成什么样，不想翻。
import type { MenuAction } from '@/components/common/menuAction'
import type { NavTarget } from '@/lib/navTarget'
import type { Routine, RoutineRun, RoutineStateKind } from '@/lib/routine'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { useRowMenu } from '@/composables/useRowMenu'

import NavLink from '../common/NavLink.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import {
  creditsLabel,
  routineFileName,
  routineRunLabel,
  routineRunTime,
  routineStateIcon,
  routineStateKind,
  routineStateText,
  routineWhenLine,
} from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    routine: Routine
    /** 展开着的那一条的执行记录。没展开就是空数组。 */
    runs?: RoutineRun[]
    /** 执行记录此刻展不展开。 */
    open?: boolean
    /** 这条规则此刻在做的那件事（`pause` / `resume` / `run-now`），没有就是空串。 */
    busy?: string
    /** 这个房间叫什么。项目总览页给（一条规则属于哪个房间是那一页要紧的信息之一）；
     *  房间右侧那一格不给 —— 那一格里的每一条都是这个房间的。 */
    roomName?: string
    /** 结果文件那几行整串点开去哪儿。项目页给（指回规则所在的房间 —— 那一页上没有
     *  能开文件的工作面板）；房间面板里那一格不给，那里的每一份都当场点开。 */
    roomTo?: NavTarget
  }>(),
  { runs: () => [], open: false, busy: '', roomName: undefined, roomTo: undefined }
)

const emit = defineEmits<{
  (e: 'pause'): void
  (e: 'resume'): void
  (e: 'run-now'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'toggle-runs'): void
  /** 展开的那三次看完，去整页：那一页会翻页、能只筛没能执行的。 */
  (e: 'open-records'): void
  /** 点了某次执行交回的一份结果文件：打开它。只有房间里那一格会给这件事接去处。 */
  (e: 'open-file', path: string): void
}>()

/** 这条规则此刻怎么了：图形和颜色都跟着它走。 */
const kind = computed<RoutineStateKind>(() => routineStateKind(props.routine))
/**
 * 「什么时候跑」。停着的规则说它为什么停（话题归档了，没有下一回）；其余照后端那句话，
 * 只是把括号里的时区换成人读的说法（见 `routineWhenLine`）。
 */
const whenLine = computed(() =>
  props.routine.room_archived ? t('routines.archivedStopped') : routineWhenLine(props.routine)
)
const roomLine = computed(() => [props.roomName, whenLine.value].filter(Boolean).join(' · '))
/**
 * 这一行说的第二句：什么时候跑，外加本月花了多少。
 *
 * 「本月」这一句**不点开也能看到** —— 它回答的是「哪条规则在花钱」，那正是列表该
 * 一眼看出来的。数只有一位小数，长的加起来也不会把这行撑开。没花过写 0（刚建好的
 * 规则就是 0），不是省掉不说。
 */
const summaryLine = computed(() =>
  [
    roomLine.value,
    t('routines.spend.thisMonth', {
      points: creditsLabel(props.routine.credits_this_month),
    }),
  ]
    .filter(Boolean)
    .join(' · ')
)
const running = computed(() => props.routine.state === 'active')
const busyOn = (action: string) => props.busy === action

/** 展开的那一段只画最近三次；再多就有「看全部」。 */
const RECENT = 3
const shownRuns = computed(() => props.runs.slice(0, RECENT))
const hasMore = computed(() => props.runs.length > RECENT)

/**
 * 「原因 ▸」：每次执行各自收放。没能执行的那几次才有 —— 跑成了的没什么好问的。
 * 收着的人不看原因（一行够长了），要问就点开。
 */
const whyOpen = ref<Set<string>>(new Set())
function toggleWhy(run: RoutineRun) {
  const next = new Set(whyOpen.value)
  if (next.has(run.id)) next.delete(run.id)
  else next.add(run.id)
  whyOpen.value = next
}
const missed = (run: RoutineRun) => run.status === 'failed' || run.status === 'skipped'

const { mdAndUp } = useDisplay()
// 桌面上右键一行弹的和 ⋯ 里那份是同一份，弹在鼠标那一点上。
const rowMenu = useRowMenu<'row'>()

/** 行尾 ⋯ 里那几件。设计稿上就没有图标：每一行都是两个字，读得懂，图形只是噪声。 */
const actions = computed<MenuAction[]>(() =>
  [
    running.value
      ? {
          key: 'pause',
          label: t('routines.action.pause'),
          loading: busyOn('pause'),
          onSelect: () => emit('pause'),
        }
      : {
          key: 'resume',
          label: t('routines.action.resume'),
          loading: busyOn('resume'),
          onSelect: () => emit('resume'),
        },
    {
      key: 'run',
      label: t('routines.action.runNow'),
      loading: busyOn('run-now'),
      onSelect: () => emit('run-now'),
    },
    { key: 'edit', label: t('routines.action.edit'), onSelect: () => emit('edit') },
    {
      key: 'runs',
      label: props.open ? t('routines.action.hideRuns') : t('routines.action.showRuns'),
      onSelect: () => emit('toggle-runs'),
    },
    {
      key: 'delete',
      label: t('routines.action.delete'),
      danger: true,
      // 删除不可撤销（记录一起走）：和上面几件用一条线分开。
      divider: true,
      onSelect: () => emit('delete'),
    },
  ].filter(
    (a) =>
      (a.key !== 'delete' || props.routine.can_delete) &&
      // 还没定触发方式的规则没有「恢复」：恢复它只会挂着「运行中」却永不响。
      // 让它跑起来的动作是「修改」里把触发方式定下来。
      (a.key !== 'resume' || props.routine.trigger)
  )
)
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's focused controls (the keyboard's Menu key fires it too) -->
  <li class="routine-row" :data-routine="routine.id" @contextmenu="mdAndUp && rowMenu.open('row', $event)">
    <AdaptiveMenu v-if="mdAndUp" v-bind="rowMenu.bind('row')" :actions="actions" :title="routine.title">
      <template #activator />
    </AdaptiveMenu>
    <div class="routine-row__head">
      <div class="routine-row__id">
        <div class="t-body routine-row__title">{{ routine.title || t('routines.unnamed') }}</div>
        <div v-if="summaryLine" class="t-meta c-faint" data-testid="routine-row-summary">{{ summaryLine }}</div>
      </div>
      <span
        class="routine-row__state"
        :class="`routine-row__state--${kind}`"
        role="img"
        :aria-label="routineStateText(kind)"
      >
        <v-icon :icon="routineStateIcon(kind)" size="16" aria-hidden="true" />
      </span>
      <AdaptiveMenu :actions="actions" :title="routine.title">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="sm"
            class="tap-target"
            :aria-label="t('routines.action.more')"
          />
        </template>
      </AdaptiveMenu>
    </div>

    <div v-if="open" class="routine-runs">
      <p v-if="!runs.length" class="t-meta c-faint routine-runs__none">{{ t('routines.neverRun') }}</p>
      <ul v-else class="routine-runs__list">
        <li v-for="run in shownRuns" :key="run.id" class="routine-run">
          <span class="t-meta c-faint">{{ routineRunTime(run.scheduled_for || run.created_at) }}</span>
          <span class="routine-run__what">
            <span class="t-body routine-run__status">{{ routineRunLabel(run.status) }}</span>
            <!-- 这一次花了多少点。没有了是「结算中」（刚跑完还没落账），不是 0。 -->
            <span v-if="run.credits != null" class="t-meta c-faint routine-run__spend" data-testid="routine-run-spend">
              {{ creditsLabel(run.credits) }}
            </span>
            <!-- 没能执行的这几次：原因收在「原因 ▸」后面。 -->
            <template v-if="missed(run)">
              <button
                type="button"
                class="routine-run__why"
                :aria-expanded="whyOpen.has(run.id) ? 'true' : 'false'"
                @click="toggleWhy(run)"
              >
                {{ t('routines.records.reason') }}
                <span class="routine-run__caret" aria-hidden="true">{{ whyOpen.has(run.id) ? '▾' : '▸' }}</span>
              </button>
              <span v-if="whyOpen.has(run.id)" class="t-meta c-danger">{{ run.error || t('routines.noReason') }}</span>
            </template>
            <!-- 跑成了的这几次：结果那一份文件名。记录里写下的是它在频道文件区的路径。 -->
            <template v-else-if="run.outputs.length">
              <NavLink v-if="roomTo" :to="roomTo" class="routine-run__file">
                {{ run.outputs.map(routineFileName).join(t('routines.listSeparator')) }}
              </NavLink>
              <template v-else>
                <button
                  v-for="path in run.outputs"
                  :key="path"
                  type="button"
                  class="routine-run__file"
                  :title="path"
                  @click="emit('open-file', path)"
                >
                  {{ routineFileName(path) }}
                </button>
              </template>
            </template>
            <span v-else-if="run.summary" class="t-meta c-muted">{{ run.summary }}</span>
          </span>
        </li>
      </ul>
      <button v-if="hasMore" type="button" class="routine-runs__all" @click="emit('open-records')">
        {{ t('routines.records.all') }}
      </button>
    </div>
  </li>
</template>

<style scoped>
.routine-row {
  padding: 12px;
  background: var(--surface);
}
/* 一条规则和上一条之间那一条线：整串是一个卡片（见 RoutineBoard），行自己不画框。 */
.routine-row + .routine-row {
  border-top: 1px solid var(--line);
}
.routine-row.row--focus {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}
.routine-row__head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.routine-row__id {
  flex: 1;
  min-width: 0;
}
.routine-row__title {
  color: var(--text);
}
/* 状态：一颗圆里的一个图形。四档四色，色从不是唯一的信号 —— 图形本身说的是同一件事。 */
.routine-row__state {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--fill);
  color: var(--muted);
}
.routine-row__state--active {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.routine-row__state--queued {
  background: color-mix(in srgb, var(--signal-blue) 14%, transparent);
  color: var(--signal-blue);
}
.routine-row__state--missed {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.routine-runs {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
}
.routine-runs__none {
  margin: 0;
}
/* 两列：左边是「哪一次的」，右边是「那一次怎么了」。列宽由最长的那一格定，三次的时间
   长短不一也对得齐（`display: contents` 让每一次的两格直接进这两列）。 */
.routine-runs__list {
  display: grid;
  grid-template-columns: max-content 1fr;
  align-items: baseline;
  gap: 8px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.routine-run {
  display: contents;
}
.routine-run__what {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  min-width: 0;
}
.routine-run__status {
  color: var(--text);
}
/* 这一次花了多少点：和「已完成」并排，安静一档。 */
.routine-run__spend {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
/* 「原因 ▸」：一个箭头，点开看那句话。 */
.routine-run__why {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
}
.routine-run__why:hover {
  color: var(--ink);
}
.routine-run__caret {
  font-size: 12px;
}
/* 一份结果文件：一颗文字按钮，点开它就打开了文件。下划线是说这件事，不是装饰。 */
.routine-run__file {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent);
  text-underline-offset: 3px;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.routine-run__file:hover {
  color: var(--accent-ink);
}
/* 看全部：整页那条路的入口。*/
.routine-runs__all {
  display: block;
  width: 100%;
  margin-top: 8px;
  padding: 8px 0 0;
  border: 0;
  border-top: 1px solid var(--line);
  background: none;
  font: inherit;
  color: var(--accent);
  text-align: start;
  cursor: pointer;
}
.routine-runs__all:hover {
  color: var(--accent-ink);
}
</style>
