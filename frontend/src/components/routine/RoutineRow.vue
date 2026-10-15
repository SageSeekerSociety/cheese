<script setup lang="ts">
// 一条规则。项目总览页和房间右侧那一格画的是同一条：名字、什么时候跑、下次什么时候
// 跑、以及此刻你能对它做什么。
//
// 只吃 props（`Routine` 的形状在 `lib/routine.ts`：画的那一半碰不得 API 层），动作一律
// 往上抛 —— 「谁去调接口」是页面的事。**删得掉删不掉由这条规则自己说**：`can_delete` 是
// 后端逐条算好的（规则主人或项目管理员），前端不猜第二遍；其余的动手都不再问一次权限，
// 读得到就改得动。
//
// 操作摆在哪儿按屏幕分：桌面上是行里的一排文字按钮，手机上收进行首的 ⋯（五颗文字按钮
// 在窄屏上要折成两三行）；右键一行弹的是同一份，弹在鼠标那一点上。
import type { MenuAction } from '@/components/common/menuAction'
import type { NavTarget } from '@/lib/navTarget'
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import { useRowMenu } from '@/composables/useRowMenu'

import NavLink from '../common/NavLink.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { responseText } from '@/lib/noticeText'
import { formatRoutineTime, routineRunLabel, routineStateLabel } from '@/lib/routine'

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
  /** 点了某次执行交回的一份结果文件：打开它。只有房间里那一格会给这件事接去处。 */
  (e: 'open-file', path: string): void
}>()

const stateLabel = computed(() => routineStateLabel(props.routine.state))
const running = computed(() => props.routine.state === 'active')
/** 「下次 …」只在真有个下次的时候才说：随话题归档停下的规则没有下一回。 */
const whenText = computed(() => {
  if (props.routine.room_archived) return t('routines.archivedStopped')
  if (!running.value || props.routine.trigger !== 'schedule') return ''
  const at = formatRoutineTime(props.routine.next_run_at, props.routine.timezone)
  return at === '—' ? '' : t('routines.next', { time: at })
})
const busyOn = (action: string) => props.busy === action

const { mdAndUp } = useDisplay()
// 手机上一行的操作收进行首的 ⋯（底部面板）。
function rowActions(): MenuAction[] {
  return [
    running.value
      ? {
          key: 'pause',
          label: t('routines.action.pause'),
          icon: 'mdi-pause',
          loading: busyOn('pause'),
          onSelect: () => emit('pause'),
        }
      : {
          key: 'resume',
          label: t('routines.action.resume'),
          icon: 'mdi-play',
          loading: busyOn('resume'),
          onSelect: () => emit('resume'),
        },
    {
      key: 'run',
      label: t('routines.action.runNow'),
      icon: 'mdi-play-circle-outline',
      loading: busyOn('run-now'),
      onSelect: () => emit('run-now'),
    },
    { key: 'edit', label: t('routines.action.edit'), icon: 'mdi-pencil-outline', onSelect: () => emit('edit') },
    {
      key: 'runs',
      label: props.open ? t('routines.action.hideRuns') : t('routines.action.showRuns'),
      icon: 'mdi-history',
      onSelect: () => emit('toggle-runs'),
    },
    {
      key: 'delete',
      label: t('routines.action.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => emit('delete'),
    },
  ].filter((a) => a.key !== 'delete' || props.routine.can_delete)
}
/** 手机上摆在 ⋯ 里的那几件（桌面摆在行里）。 */
const inMenu = computed(() => (mdAndUp.value ? [] : rowActions()))
// 桌面上操作摆在行里，右键一行弹的是同一份，弹在鼠标那一点上。
const rowMenu = useRowMenu<'row'>()
</script>

<template>
  <li class="routine-row" :data-routine="routine.id" @contextmenu="mdAndUp && rowMenu.open('row', $event)">
    <AdaptiveMenu v-if="mdAndUp" v-bind="rowMenu.bind('row')" :actions="rowActions()" :title="routine.title">
      <template #activator />
    </AdaptiveMenu>
    <div class="routine-row__head">
      <div class="routine-row__id">
        <div class="t-body routine-row__title">{{ routine.title }}</div>
        <div class="t-meta c-faint">
          {{ responseText(routine, 'trigger_text') }}<template v-if="roomName"> · {{ roomName }}</template>
          <template v-if="whenText"> · {{ whenText }}</template>
        </div>
      </div>
      <v-chip size="small" :color="running ? 'success' : undefined" variant="tonal">
        {{ stateLabel }}
      </v-chip>
      <AdaptiveMenu v-if="inMenu.length" :actions="inMenu" :title="routine.title">
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

    <div v-if="mdAndUp" class="routine-row__actions">
      <BaseButton v-if="running" kind="ghost" size="sm" :loading="busyOn('pause')" @click="emit('pause')">
        {{ t('routines.action.pause') }}
      </BaseButton>
      <BaseButton v-else kind="ghost" size="sm" :loading="busyOn('resume')" @click="emit('resume')">
        {{ t('routines.action.resume') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" :loading="busyOn('run-now')" @click="emit('run-now')">
        {{ t('routines.action.runNow') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" @click="emit('edit')">{{ t('routines.action.edit') }}</BaseButton>
      <BaseButton kind="ghost" size="sm" @click="emit('toggle-runs')">
        {{ open ? t('routines.action.hideRuns') : t('routines.action.showRuns') }}
      </BaseButton>
      <BaseButton v-if="routine.can_delete" kind="ghost" size="sm" @click="emit('delete')">
        {{ t('routines.action.delete') }}
      </BaseButton>
    </div>

    <div v-if="open" class="routine-runs">
      <p v-if="!runs.length" class="t-meta c-faint">{{ t('routines.neverRun') }}</p>
      <ol v-else class="routine-runs__list">
        <li v-for="run in runs" :key="run.id" class="routine-run">
          <div class="routine-run__head t-meta">
            <v-chip
              size="x-small"
              variant="tonal"
              :color="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : undefined"
            >
              {{ routineRunLabel(run.status) }}
            </v-chip>
            <span>{{ formatRoutineTime(run.scheduled_for || run.created_at, routine.timezone) }}</span>
            <span v-if="run.finished_at" class="c-faint">
              {{ t('routines.finishedAt', { time: formatRoutineTime(run.finished_at, routine.timezone) }) }}
            </span>
          </div>
          <div class="t-meta c-muted">{{ run.trigger_detail }}</div>
          <div v-if="run.status === 'failed' || run.status === 'skipped'" class="t-body c-danger">
            {{ run.error || t('routines.noReason') }}
          </div>
          <div v-else-if="run.summary" class="t-body">{{ run.summary }}</div>
          <!-- 这次执行交回的结果文件。记录里写下的只是它们当时的路径，频道面板上没有对应
               的文件夹 —— 房间里这一格就是它们的入口，所以每一份都当场点得开；项目页上
               没有能开文件的面板，整串指回规则所在的频道。 -->
          <div v-if="run.outputs.length" class="t-meta routine-run__outputs">
            <span class="routine-run__outputs-label">{{ t('routines.outputs') }}</span>
            <NavLink v-if="roomTo" :to="roomTo">{{ run.outputs.join(t('routines.listSeparator')) }}</NavLink>
            <template v-else>
              <button
                v-for="path in run.outputs"
                :key="path"
                type="button"
                class="routine-run__file"
                :title="path"
                @click="emit('open-file', path)"
              >
                {{ path.split('/').pop() || path }}
              </button>
            </template>
          </div>
        </li>
      </ol>
    </div>
  </li>
</template>

<style scoped>
.routine-row {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.routine-row.row--focus {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.routine-row__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.routine-row__id {
  flex: 1;
  min-width: 0;
}
.routine-row__title {
  color: var(--text);
}
.routine-row__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}
.routine-runs {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
}
.routine-runs__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.routine-run__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.routine-run__outputs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
}
/* 一份结果文件：一颗文字按钮，点开它就打开了文件。下划线是说这件事，不是装饰。 */
.routine-run__file {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  text-decoration: underline;
  text-decoration-color: var(--line);
  text-underline-offset: 3px;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.routine-run__file:hover {
  color: var(--ink);
}
</style>
