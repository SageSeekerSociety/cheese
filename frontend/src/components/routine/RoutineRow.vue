<script setup lang="ts">
// 一条规则。项目总览页和房间右侧那一格画的是同一条：名字、什么时候跑、上次什么时候
// 跑的、结果放哪、等不等你确认，以及此刻你能对它做什么。
//
// 只吃 props（`Routine` 的形状在 `lib/routine.ts`：画的那一半碰不得 API 层），动作一律
// 往上抛 —— 「谁去调接口」是页面的事。**按钮亮不亮由这条规则自己说**：`can_manage` 是
// 后端逐条算好的（规则主人或项目管理员），前端不猜第二遍。
//
// 「谁能动什么」是三档，不是两档：频道里的人都能改、能暂停/恢复、能立即执行一次；只有
// 规则主人和项目管理员能确认启用、能删除。做不到的按钮干脆不出现，也不写一句解释。
//
// 待确认的那一条整条摊开：工作内容、存储位置、执行条件、执行范围都摆在行里读（点
// 「确认启用」之前这是他唯一一次读到），三颗按钮直接摆在外面，行尾没有 ⋯。已经在跑的
// 那一条，动作全收在行尾那颗 ⋯ 里 —— 暂停（已暂停的给恢复）/ 立即执行一次 / 修改 /
// 执行记录 / 删除。
import type { MenuAction } from '@/components/common/menuAction'
import type { NavTarget } from '@/lib/navTarget'
import type { Routine, RoutineRun } from '@/lib/routine'

import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import NavLink from '../common/NavLink.vue'
import RoutineRunRow from './RoutineRunRow.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { useRowMenu } from '@/composables/useRowMenu'
import { t } from '@/i18n'
import { responseText } from '@/lib/noticeText'
import { routineLastRunText, routineStateLabel } from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    routine: Routine
    /** 展开着的那一条的执行记录。没展开就是空数组。 */
    runs?: RoutineRun[]
    /** 执行记录此刻展不展开。 */
    open?: boolean
    /** 这条规则此刻在做的那件事（`confirm` / `pause` / ...），没有就是空串。 */
    busy?: string
    /** 这条规则所在的频道叫什么。项目总览页给（一条规则属于哪个频道是那一页要紧的
     *  信息之一）；房间右侧那一格不给 —— 那一格里的每一条都是这个房间的，写一遍是
     *  多余的。 */
    roomName?: string
    /** 「执行记录」那个整页的去处。给了就照它画；不给就不要这条（零插件的宿主里点不开）。 */
    runsTo?: NavTarget
    /** 结果文件那几行的去处。项目总览页给（指回规则所在的频道 —— 那一页上没有能把文件
     *  摊开的工作面板，去了那儿才点得开）；房间右侧那一格不给，那里的每一份都当场点开。 */
    roomTo?: NavTarget
  }>(),
  {
    runs: () => [],
    open: false,
    busy: '',
    roomName: undefined,
    runsTo: undefined,
    roomTo: undefined,
  }
)

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'pause'): void
  (e: 'resume'): void
  (e: 'run-now'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'toggle-runs'): void
  /** 点了某次执行交回的一份结果文件：打开它。 */
  (e: 'open-file', path: string): void
}>()

const draft = computed(() => props.routine.state === 'draft')
const stateLabel = computed(() => routineStateLabel(props.routine.state))
const running = computed(() => props.routine.state === 'active')
/** 头一行那串：执行条件（后端写好的整句，时区跟在时间后面）。 */
const whenText = computed(() => responseText(props.routine, 'trigger_text'))
/** 这一次已经排上、或者正在跑：行尾挂一颗「排队中」，不再说「上次执行 …」—— 那件事
 *  正在发生，说它「上次」是另一件事。 */
const queued = computed(() => props.routine.last_run?.status === 'queued')
const runningNow = computed(() => props.routine.last_run?.status === 'running')
/** 「上次执行 …」：只在最近那一次跑完（或者没跑过）的时候说。 */
const lastText = computed(() => {
  if (queued.value || runningNow.value) return ''
  // 频道归档了，这条就停在这儿了：这件事比「上次什么时候跑的」要紧，占它那一格。
  if (props.routine.room_archived) return t('routines.archivedStopped')
  return routineLastRunText(props.routine)
})
const busyOn = (action: string) => props.busy === action
/** 某个频道名是不是已经没了（后端查不到的给空标题）。 */
const goneRoom = (title: string) => !title

const { mdAndUp } = useDisplay()
// 桌面上右键一行，弹的是行尾那颗 ⋯ 里同一份东西，弹在鼠标那一点上。
const rowMenu = useRowMenu<'row'>()

/**
 * 已经在跑的那一条，动作全收在这颗 ⋯ 里 —— 五样东西摊在行里会把一条规则撑成三行，
 * 而它们平时一件都不用人管。做不到的（删除）不进这份清单。
 */
const actions = computed<MenuAction[]>(() => {
  if (draft.value) return []
  const list: MenuAction[] = [
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
  ]
  if (props.routine.can_manage) {
    list.push({
      key: 'delete',
      label: t('routines.action.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => emit('delete'),
    })
  }
  return list
})
</script>

<template>
  <li
    class="routine-row"
    :class="{ 'routine-row--draft': draft }"
    :data-routine="routine.id"
    @contextmenu="mdAndUp && actions.length && rowMenu.open('row', $event)"
  >
    <!-- 右键那一份：桌面上没有实体按钮，菜单自己不出来。 -->
    <AdaptiveMenu v-if="mdAndUp && actions.length" v-bind="rowMenu.bind('row')" :actions="actions" :title="routine.title">
      <template #activator />
    </AdaptiveMenu>
    <div class="routine-row__head">
      <div class="routine-row__id">
        <div class="t-body routine-row__title">{{ routine.title }}</div>
        <!-- 头一行：哪个频道 · 什么时候跑 · 结果放哪 · 上次什么时候跑的。待确认那条不写，
             它把这几样摊在下面给人读。 -->
        <div v-if="!draft" class="t-meta c-faint">
          <template v-if="roomName">{{ roomName }} · </template>
          {{ whenText }}
          <template v-if="routine.output_dir"> · {{ routine.output_dir }}/</template>
          <template v-if="lastText"> · {{ lastText }}</template>
        </div>
      </div>
      <v-chip size="small" :color="draft ? 'warning' : running ? 'success' : undefined" variant="tonal">
        {{ stateLabel }}
      </v-chip>
      <!-- 上一次还在跑，这一次又到点了：排在上一次后面，行里挂一颗「排队中」——
           不跳过，也不并着跑。 -->
      <v-chip v-if="queued" size="small" variant="tonal">{{ t('routines.run.queued') }}</v-chip>
      <AdaptiveMenu v-if="actions.length" :actions="actions" :title="routine.title">
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

    <!-- 待确认：摊开给人读的那几样。点「确认启用」之前这是他唯一一次读到。 -->
    <dl v-if="draft" class="routine-row__spec t-meta">
      <dt>{{ t('routines.spec.work') }}</dt>
      <dd>{{ routine.instructions }}</dd>
      <dt>{{ t('routines.spec.output') }}</dt>
      <dd>{{ routine.output_dir ? `${routine.output_dir}/` : t('routines.spec.rootDir') }}</dd>
      <dt>{{ t('routines.spec.trigger') }}</dt>
      <dd>{{ whenText }}</dd>
      <dt>{{ t('routines.spec.range') }}</dt>
      <dd v-if="!routine.scope_topics.length">{{ t('routines.spec.unlimited') }}</dd>
      <dd v-else>
        <template v-for="(room, i) in routine.scope_topics" :key="room.id">
          <span v-if="i" aria-hidden="true">{{ t('routines.listSeparator') }}</span>
          <span v-if="goneRoom(room.title)">{{ t('routines.goneRoom') }}</span>
          <span v-else>{{ room.title }}</span>
        </template>
      </dd>
    </dl>

    <!-- 待确认那条的三颗按钮：这一行唯一需要人动手的东西，所以摆在行里，不折进 ⋯。 -->
    <div v-if="draft" class="routine-row__actions">
      <BaseButton v-if="routine.can_manage" kind="primary" size="sm" :loading="busyOn('confirm')" @click="emit('confirm')">
        {{ t('routines.action.confirm') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" @click="emit('edit')">{{ t('routines.action.edit') }}</BaseButton>
      <BaseButton v-if="routine.can_manage" kind="ghost" size="sm" @click="emit('delete')">
        {{ t('routines.action.delete') }}
      </BaseButton>
    </div>

    <!-- 执行记录：面板里只有最近 5 次，底下一条「看全部 {count} 次」开整页。记录再多也不
         在行里长 —— 右侧面板旁边还有别的东西，铺长了会把它挤没。 -->
    <div v-if="open && !draft" class="routine-runs">
      <p v-if="!runs.length" class="t-meta c-faint">{{ t('routines.neverRun') }}</p>
      <ol v-else class="routine-runs__list">
        <li v-for="run in runs.slice(0, 5)" :key="run.id" class="routine-run">
          <RoutineRunRow
            :run="run"
            :timezone="routine.timezone"
            :room-to="roomTo"
            @open-file="(path) => emit('open-file', path)"
          />
        </li>
      </ol>
      <NavLink v-if="runsTo" :to="runsTo" class="routine-runs__all t-meta">
        {{ t('routines.runs.all', { count: routine.run_count }) }} ›
      </NavLink>
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
.routine-row--draft {
  border-color: rgb(var(--v-theme-warning));
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
.routine-row__spec {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 12px;
  margin: 8px 0;
}
.routine-row__spec dt {
  color: var(--faint);
}
.routine-row__spec dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.routine-row__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
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
.routine-runs__all {
  display: inline-block;
  margin-top: 8px;
  color: var(--muted);
  text-decoration: none;
}
.routine-runs__all:hover {
  color: var(--ink);
}
</style>
