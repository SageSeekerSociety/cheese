<script setup lang="ts">
// 一条规则。项目总览页和房间右侧那一格画的是同一条：名字、什么时候跑、下次什么时候
// 跑、等不等你确认、以及此刻你能对它做什么。
//
// 只吃 props（`Routine` 的形状在 `lib/routine.ts`：画的那一半碰不得 API 层），动作一律
// 往上抛 —— 「谁去调接口」是页面的事。**按钮亮不亮由这条规则自己说**：`can_manage` 是
// 后端逐条算好的（规则主人或项目管理员），前端不猜第二遍。
//
// 等确认的那一条和已经在跑的不是同一个样子：草稿要把「要做什么、用哪些资料、结果放哪」
// 摊开给人读（点「确认启用」之前这是他唯一一次读到），而且它唯一要紧的动作就是确认；
// 已经在跑的那一条，手机上一行的操作收进行首的 ⋯（五颗文字按钮在窄屏上要折成两三行）。
import type { MenuAction } from '@/components/common/menuAction'
import type { NavTarget } from '@/lib/navTarget'
import type { Routine, RoutineRun } from '@/lib/routine'
import type { UserRefTarget } from '@/lib/userRef'

import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import NavLink from '../common/NavLink.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserRef from '@/components/common/UserRef.vue'
import { formatRoutineTime, ROUTINE_RUN_LABEL, ROUTINE_STATE_LABEL } from '@/lib/routine'
import { userRefRoute } from '@/lib/userRef'

const props = withDefaults(
  defineProps<{
    routine: Routine
    /** 展开着的那一条的执行记录。没展开就是空数组。 */
    runs?: RoutineRun[]
    /** 执行记录此刻展不展开。 */
    open?: boolean
    /** 这条规则此刻在做的那件事（`confirm` / `pause` / ...），没有就是空串。 */
    busy?: string
    /** 这个房间叫什么。项目总览页给（一条规则属于哪个房间是那一页要紧的信息之一）；
     *  房间右侧那一格不给 —— 那一格里的每一条都是这个房间的。 */
    roomName?: string
    /** 执行结果那几行点开去哪儿。不给就只写路径（房间面板里那份文件就在手边，
     *  点它没有别的地方可去）。 */
    roomTo?: NavTarget
    /** 人名 → 显示名。名册查询在外面（`useUserRef` 那条路），这一行只收结果；
     *  查不到的画 handle 本身。 */
    userNames?: Record<string, string>
  }>(),
  { runs: () => [], open: false, busy: '', roomName: undefined, roomTo: undefined, userNames: () => ({}) }
)

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'pause'): void
  (e: 'resume'): void
  (e: 'run-now'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'toggle-runs'): void
  /** 点了行里的某个人名：去他的成员页 / 主页。跳路由是最外层的事。 */
  (e: 'navigate', target: UserRefTarget): void
}>()

const nameOf = (h: string) => props.userNames[h] || h
const targetOf = (h: string) => userRefRoute(h, props.routine.project_id)

const draft = computed(() => props.routine.state === 'draft')
const stateLabel = computed(() => ROUTINE_STATE_LABEL[props.routine.state])
const running = computed(() => props.routine.state === 'active')
/** 「下次 …」只在真有个下次的时候才说：随话题归档停下的规则没有下一回。 */
const whenText = computed(() => {
  if (props.routine.room_archived) return '已随话题归档停止'
  if (!running.value || props.routine.trigger !== 'schedule') return ''
  const at = formatRoutineTime(props.routine.next_run_at, props.routine.timezone)
  return at === '—' ? '' : `下次 ${at}`
})
const busyOn = (action: string) => props.busy === action

const { mdAndUp } = useDisplay()
// 手机上一行的操作收进行首的 ⋯（底部面板）。等确认的那一条，「确认启用」仍然摆在
// 行里——那是这一行唯一要紧的事。
function rowActions(): MenuAction[] {
  if (draft.value)
    return [
      { key: 'edit', label: '修改', icon: 'mdi-pencil-outline', onSelect: () => emit('edit') },
      { key: 'drop', label: '不要了', icon: 'mdi-delete-outline', danger: true, onSelect: () => emit('delete') },
    ]
  return [
    running.value
      ? { key: 'pause', label: '暂停', icon: 'mdi-pause', loading: busyOn('pause'), onSelect: () => emit('pause') }
      : { key: 'resume', label: '恢复', icon: 'mdi-play', loading: busyOn('resume'), onSelect: () => emit('resume') },
    {
      key: 'run',
      label: '立即执行一次',
      icon: 'mdi-play-circle-outline',
      loading: busyOn('run-now'),
      onSelect: () => emit('run-now'),
    },
    { key: 'edit', label: '修改', icon: 'mdi-pencil-outline', onSelect: () => emit('edit') },
    {
      key: 'runs',
      label: props.open ? '收起记录' : '执行记录',
      icon: 'mdi-history',
      onSelect: () => emit('toggle-runs'),
    },
    { key: 'delete', label: '删除', icon: 'mdi-delete-outline', danger: true, onSelect: () => emit('delete') },
  ]
}
/** 手机上摆在 ⋯ 里的那几件（桌面摆在行里）。 */
const inMenu = computed(() => (mdAndUp.value ? [] : rowActions()))
</script>

<template>
  <li class="routine-row" :class="{ 'routine-row--draft': draft }" :data-routine="routine.id">
    <div class="routine-row__head">
      <div class="routine-row__id">
        <div class="t-body routine-row__title">{{ routine.title }}</div>
        <div class="t-meta c-faint">
          {{ routine.trigger_text }}<template v-if="roomName"> · {{ roomName }}</template>
          <template v-if="whenText"> · {{ whenText }}</template>
        </div>
      </div>
      <v-chip size="small" :color="draft ? 'warning' : running ? 'success' : undefined" variant="tonal">
        {{ stateLabel }}
      </v-chip>
      <AdaptiveMenu v-if="routine.can_manage && inMenu.length" :actions="inMenu" :title="routine.title">
        <template #activator="{ props: menuProps }">
          <v-btn
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="small"
            variant="text"
            color="on-surface-variant"
            class="tap-target"
            aria-label="更多操作"
          />
        </template>
      </AdaptiveMenu>
    </div>

    <!-- 草稿：摊开给人读的那几样。 -->
    <dl v-if="draft" class="routine-row__spec t-meta">
      <dt>工作内容</dt>
      <dd>{{ routine.instructions }}</dd>
      <dt>资料范围</dt>
      <dd>{{ routine.context_scope || '未限定' }}</dd>
      <dt>结果放在</dt>
      <dd>房间 {{ routine.output_dir || '根目录' }}</dd>
      <dt>执行者</dt>
      <dd>
        <UserRef
          :handle="routine.agent_handle"
          :name="nameOf(routine.agent_handle)"
          :to="targetOf(routine.agent_handle)"
          @navigate="emit('navigate', targetOf(routine.agent_handle))"
        />（由
        <UserRef
          :handle="routine.proposed_by"
          :name="nameOf(routine.proposed_by)"
          :to="targetOf(routine.proposed_by)"
          @navigate="emit('navigate', targetOf(routine.proposed_by))"
        />
        起草）
      </dd>
    </dl>

    <div v-if="routine.can_manage" class="routine-row__actions">
      <template v-if="draft">
        <v-btn size="small" color="primary" variant="flat" :loading="busyOn('confirm')" @click="emit('confirm')">
          确认启用
        </v-btn>
        <template v-if="mdAndUp">
          <v-btn size="small" variant="text" @click="emit('edit')">修改</v-btn>
          <v-btn size="small" variant="text" color="on-surface-variant" @click="emit('delete')">不要了</v-btn>
        </template>
      </template>
      <template v-else-if="mdAndUp">
        <v-btn v-if="running" size="small" variant="text" :loading="busyOn('pause')" @click="emit('pause')">
          暂停
        </v-btn>
        <v-btn v-else size="small" variant="text" :loading="busyOn('resume')" @click="emit('resume')">恢复</v-btn>
        <v-btn size="small" variant="text" :loading="busyOn('run-now')" @click="emit('run-now')">立即执行一次</v-btn>
        <v-btn size="small" variant="text" @click="emit('edit')">修改</v-btn>
        <v-btn size="small" variant="text" @click="emit('toggle-runs')">
          {{ open ? '收起记录' : '执行记录' }}
        </v-btn>
        <v-btn size="small" variant="text" color="on-surface-variant" @click="emit('delete')">删除</v-btn>
      </template>
    </div>
    <!-- 别人的规则：一颗按钮都不画，但要说清为什么 —— 一条没有按钮的规则和一条你没
         权限的规则，看起来不该是同一个东西。 -->
    <p v-else class="t-meta c-faint routine-row__readonly">
      这条规则由
      <UserRef
        :handle="routine.owner_handle"
        :name="nameOf(routine.owner_handle)"
        :to="targetOf(routine.owner_handle)"
        @navigate="emit('navigate', targetOf(routine.owner_handle))"
      />
      管，只有他和项目管理员能改
    </p>

    <div v-if="open && !draft" class="routine-runs">
      <p v-if="!runs.length" class="t-meta c-faint">还没有执行过</p>
      <ol v-else class="routine-runs__list">
        <li v-for="run in runs" :key="run.id" class="routine-run">
          <div class="routine-run__head t-meta">
            <v-chip
              size="x-small"
              variant="tonal"
              :color="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : undefined"
            >
              {{ ROUTINE_RUN_LABEL[run.status] }}
            </v-chip>
            <span>{{ formatRoutineTime(run.scheduled_for || run.created_at, routine.timezone) }}</span>
            <span v-if="run.finished_at" class="c-faint">
              结束于 {{ formatRoutineTime(run.finished_at, routine.timezone) }}
            </span>
          </div>
          <div class="t-meta c-muted">{{ run.trigger_detail }}</div>
          <div v-if="run.status === 'failed' || run.status === 'skipped'" class="t-body c-danger">
            {{ run.error || '没有给出原因' }}
          </div>
          <div v-else-if="run.summary" class="t-body">{{ run.summary }}</div>
          <div v-if="run.outputs.length" class="t-meta">
            结果：
            <NavLink v-if="roomTo" :to="roomTo">{{ run.outputs.join('、') }}</NavLink>
            <template v-else>{{ run.outputs.join('、') }}</template>
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
.routine-row__readonly {
  margin: 8px 0 0;
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
</style>
