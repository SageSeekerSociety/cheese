<script setup lang="ts">
// 定时与触发：房间里的 AI 队友按时间、或在项目里发生某件事时自己开工的那些规则。
//
// 芝士起草的规则停在「待确认」，只有人在这里点「确认启用」才会开始跑：无人值守地
// 动手，得先有人读过它要做什么、用哪些资料、结果放哪。
import type { MenuAction } from '@/components/common/menuAction'
import type { Routine, RoutineInput, RoutineRun } from '../api'
import type { Topic } from '../cx_types'

import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'

import {
  createRoutine,
  deleteRoutine,
  getRoutine,
  listProjectRoutines,
  listTopics,
  routineAction,
  updateRoutine,
} from '../api'

import { useCommands } from '@/commands'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserRef from '@/components/common/UserRef.vue'
import { t } from '@/i18n'
import { focusRow } from '@/lib/focusRow'

const props = defineProps<{ projectId: string }>()
const route = useRoute()

const routines = ref<Routine[]>([])
const rooms = ref<Topic[]>([])
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const busy = ref('')
const open = ref<string | null>(null)
const runs = ref<Record<string, RoutineRun[]>>({})
const confirmingDelete = ref<Routine | null>(null)

// 手机上一行的操作收进行首的 ⋯（底部面板）：五颗文字按钮在窄屏上要折成两三行。
// 等你确认的那一条，「确认启用」仍然摆在行里——那是这一行唯一要紧的事。
const { mdAndUp } = useDisplay()
function rowActions(r: Routine): MenuAction[] {
  if (r.state === 'draft')
    return [
      { key: 'edit', label: '修改', icon: 'mdi-pencil-outline', onSelect: () => startEdit(r) },
      {
        key: 'drop',
        label: '不要了',
        icon: 'mdi-delete-outline',
        danger: true,
        onSelect: () => (confirmingDelete.value = r),
      },
    ]
  return [
    r.state === 'active'
      ? {
          key: 'pause',
          label: '暂停',
          icon: 'mdi-pause',
          loading: busy.value === `${r.id}:pause`,
          onSelect: () => void act(r, 'pause'),
        }
      : {
          key: 'resume',
          label: '恢复',
          icon: 'mdi-play',
          loading: busy.value === `${r.id}:resume`,
          onSelect: () => void act(r, 'resume'),
        },
    {
      key: 'run',
      label: '立即执行一次',
      icon: 'mdi-play-circle-outline',
      loading: busy.value === `${r.id}:run-now`,
      onSelect: () => void act(r, 'run-now'),
    },
    { key: 'edit', label: '修改', icon: 'mdi-pencil-outline', onSelect: () => startEdit(r) },
    {
      key: 'runs',
      label: open.value === r.id ? '收起记录' : '执行记录',
      icon: 'mdi-history',
      onSelect: () => void toggle(r.id),
    },
    {
      key: 'delete',
      label: '删除',
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (confirmingDelete.value = r),
    },
  ]
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const TRIGGERS = [
  { value: 'schedule', title: '按时间' },
  { value: 'library_file_added', title: '资料库新增文件时' },
  { value: 'task_closed', title: '任务完成时' },
  { value: 'card_accepted', title: '成果被采纳时' },
]
const FREQS = [
  { value: 'daily', title: '每天' },
  { value: 'weekly', title: '每周' },
  { value: 'monthly', title: '每月' },
  { value: 'hourly', title: '每小时' },
]
const STATE_LABEL: Record<Routine['state'], string> = { draft: '待确认', active: '执行中', paused: '已暂停' }
const RUN_LABEL: Record<RoutineRun['status'], string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  skipped: '未执行',
}

const roomTitle = (id: string) => rooms.value.find((r) => r.id === id)?.title ?? '（已不在的房间）'
const drafts = computed(() => routines.value.filter((r) => r.state === 'draft'))
const others = computed(() => routines.value.filter((r) => r.state !== 'draft'))

// 按规则自己的时区写：「每周一 09:00（Asia/Shanghai）」旁边的下次时间要对得上。
function fmt(iso: string | null, timeZone?: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', { timeZone, hour12: false })
  } catch {
    return new Date(iso).toLocaleString()
  }
}

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const [listed, topics] = await Promise.all([listProjectRoutines(projectId), listTopics(projectId)])
    if (props.projectId !== projectId) return
    routines.value = listed.data
    rooms.value = topics.data.filter((tp) => tp.status !== 'archived')
    const focus = typeof route.query.routine === 'string' ? route.query.routine : null
    if (focus && routines.value.some((r) => r.id === focus)) {
      loading.value = false
      void toggle(focus, true)
      void focusRow(`[data-routine="${CSS.escape(focus)}"]`)
    }
  } catch (e) {
    if (props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : '未能读取定时与触发规则'
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

async function toggle(id: string, force = false) {
  if (open.value === id && !force) {
    open.value = null
    return
  }
  open.value = id
  try {
    const detail = await getRoutine(id)
    runs.value = { ...runs.value, [id]: detail.runs }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能读取执行记录'
  }
}

async function act(r: Routine, action: 'confirm' | 'pause' | 'resume' | 'run-now') {
  busy.value = `${r.id}:${action}`
  actionError.value = ''
  try {
    const out = await routineAction(r.id, action)
    if (action === 'run-now') {
      await toggle(r.id, true)
    } else {
      routines.value = routines.value.map((x) => (x.id === r.id ? (out as Routine) : x))
    }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '操作没有成功'
  } finally {
    busy.value = ''
  }
}

async function remove(r: Routine) {
  confirmingDelete.value = null
  busy.value = `${r.id}:delete`
  actionError.value = ''
  try {
    await deleteRoutine(r.id)
    routines.value = routines.value.filter((x) => x.id !== r.id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能删除'
  } finally {
    busy.value = ''
  }
}

// ── 新建 / 编辑 ────────────────────────────────────────────────────────────
const editing = ref<Routine | 'new' | null>(null)
// 关上的那一下 editing 已经是 null，标题还要照着刚才那一条画完收起的动画。
const editingTitle = ref('')
watch(editing, (value) => {
  if (value) editingTitle.value = value === 'new' ? '新建规则' : `修改「${value.title}」`
})
const saving = ref(false)
const formError = ref('')
const form = reactive({
  room: '',
  title: '',
  instructions: '',
  context_scope: '',
  output_dir: '',
  trigger: 'schedule' as Routine['trigger'],
  freq: 'weekly',
  time: '09:00',
  weekdays: [0] as number[],
  day: 1,
  minute: 0,
  scope: 'room',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
})

function startNew() {
  const room = typeof route.query.room === 'string' ? route.query.room : rooms.value[0]?.id ?? ''
  Object.assign(form, {
    room,
    title: '',
    instructions: '',
    context_scope: '',
    output_dir: '',
    trigger: 'schedule',
    freq: 'weekly',
    time: '09:00',
    weekdays: [0],
    day: 1,
    minute: 0,
    scope: 'room',
  })
  formError.value = ''
  editing.value = 'new'
}

function startEdit(r: Routine) {
  const spec = r.spec as Record<string, unknown>
  Object.assign(form, {
    room: r.topic_id,
    title: r.title,
    instructions: r.instructions,
    context_scope: r.context_scope,
    output_dir: r.output_dir,
    trigger: r.trigger,
    freq: (spec.freq as string) ?? 'weekly',
    time: (spec.time as string) ?? '09:00',
    weekdays: (spec.weekdays as number[]) ?? [0],
    day: (spec.day as number) ?? 1,
    minute: (spec.minute as number) ?? 0,
    scope: (spec.scope as string) ?? 'room',
    timezone: r.timezone,
  })
  formError.value = ''
  editing.value = r
}

function specFromForm(): Record<string, unknown> {
  if (form.trigger !== 'schedule') return { scope: form.scope }
  if (form.freq === 'hourly') return { freq: 'hourly', minute: Number(form.minute) }
  if (form.freq === 'weekly') return { freq: 'weekly', time: form.time, weekdays: [...form.weekdays] }
  if (form.freq === 'monthly') return { freq: 'monthly', time: form.time, day: Number(form.day) }
  return { freq: 'daily', time: form.time }
}

async function save() {
  saving.value = true
  formError.value = ''
  const body: RoutineInput = {
    title: form.title,
    instructions: form.instructions,
    context_scope: form.context_scope,
    output_dir: form.output_dir,
    trigger: form.trigger,
    spec: specFromForm(),
    timezone: form.timezone,
  }
  try {
    if (editing.value === 'new') {
      if (!form.room) throw new Error('先选一个房间：工作在那个房间里执行，结果也放在那里')
      const created = await createRoutine(form.room, body)
      routines.value = [...routines.value, created]
    } else if (editing.value) {
      const updated = await updateRoutine(editing.value.id, body)
      routines.value = routines.value.map((x) => (x.id === updated.id ? updated : x))
    }
    editing.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '没有保存成功'
  } finally {
    saving.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    routines.value = []
    runs.value = {}
    open.value = null
    void load()
  },
  { immediate: true }
)
// 页头上的两件事：手机上「新建」是顶栏那一颗，「刷新」进 ⋯。
useCommands(() => [
  { id: 'routines.refresh', title: '刷新', icon: 'mdi-refresh', loading: loading.value, header: {}, run: load },
  {
    id: 'routines.new',
    title: '新建',
    icon: 'mdi-plus',
    disabled: !rooms.value.length,
    header: { primary: true, accent: true },
    run: startNew,
  },
])
</script>

<template>
  <AppPage :title="t('navigation.project.routines')">
    <div>
      <p class="t-body c-muted mb-6">
        到点或项目里发生某件事时，房间里的 AI 队友自己开工，做完把结果放进房间并通知你。
        在房间里说「每周一早上整理一次进展」，芝士也会起草一条，等你在这里确认
      </p>

      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>
      <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

      <div v-if="loading && !routines.length" class="py-8 text-center" role="status" aria-label="读取规则">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else>
        <section v-if="drafts.length" class="mb-6">
          <h2 class="t-section mb-2">等你确认</h2>
          <ul class="routine-list">
            <li v-for="r in drafts" :key="r.id" class="routine-row routine-row--draft" :data-routine="r.id">
              <div class="routine-row__head">
                <div class="routine-row__id">
                  <div class="t-body routine-row__title">{{ r.title }}</div>
                  <div class="t-meta c-faint">{{ r.trigger_text }} · {{ roomTitle(r.topic_id) }}</div>
                </div>
                <v-chip size="small" color="warning" variant="tonal">{{ STATE_LABEL[r.state] }}</v-chip>
                <AdaptiveMenu v-if="!mdAndUp" :actions="rowActions(r)" :title="r.title">
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
              <dl class="routine-row__spec t-meta">
                <dt>工作内容</dt>
                <dd>{{ r.instructions }}</dd>
                <dt>资料范围</dt>
                <dd>{{ r.context_scope || '未限定' }}</dd>
                <dt>结果放在</dt>
                <dd>房间 {{ r.output_dir || '根目录' }}</dd>
                <dt>执行者</dt>
                <dd><UserRef :handle="r.agent_handle" />（由 <UserRef :handle="r.proposed_by" /> 起草）</dd>
              </dl>
              <div class="routine-row__actions">
                <v-btn
                  size="small"
                  color="primary"
                  variant="flat"
                  :loading="busy === `${r.id}:confirm`"
                  @click="act(r, 'confirm')"
                >
                  确认启用
                </v-btn>
                <template v-if="mdAndUp">
                  <v-btn size="small" variant="text" @click="startEdit(r)">修改</v-btn>
                  <v-btn size="small" variant="text" color="on-surface-variant" @click="confirmingDelete = r">
                    不要了
                  </v-btn>
                </template>
              </div>
            </li>
          </ul>
        </section>

        <ul v-if="others.length" class="routine-list">
          <li v-for="r in others" :key="r.id" class="routine-row" :data-routine="r.id">
            <div class="routine-row__head">
              <div class="routine-row__id">
                <div class="t-body routine-row__title">{{ r.title }}</div>
                <div class="t-meta c-faint">
                  {{ r.trigger_text }} · {{ roomTitle(r.topic_id) }}
                  <template v-if="r.state === 'active' && r.trigger === 'schedule'">
                    · 下次 {{ fmt(r.next_run_at, r.timezone) }}
                  </template>
                </div>
              </div>
              <v-chip size="small" :color="r.state === 'active' ? 'success' : undefined" variant="tonal">
                {{ STATE_LABEL[r.state] }}
              </v-chip>
              <AdaptiveMenu v-if="!mdAndUp" :actions="rowActions(r)" :title="r.title">
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
            <div v-if="mdAndUp" class="routine-row__actions">
              <v-btn
                v-if="r.state === 'active'"
                size="small"
                variant="text"
                :loading="busy === `${r.id}:pause`"
                @click="act(r, 'pause')"
              >
                暂停
              </v-btn>
              <v-btn v-else size="small" variant="text" :loading="busy === `${r.id}:resume`" @click="act(r, 'resume')">
                恢复
              </v-btn>
              <v-btn size="small" variant="text" :loading="busy === `${r.id}:run-now`" @click="act(r, 'run-now')">
                立即执行一次
              </v-btn>
              <v-btn size="small" variant="text" @click="startEdit(r)">修改</v-btn>
              <v-btn size="small" variant="text" @click="toggle(r.id)">
                {{ open === r.id ? '收起记录' : '执行记录' }}
              </v-btn>
              <v-btn size="small" variant="text" color="on-surface-variant" @click="confirmingDelete = r">删除</v-btn>
            </div>
            <div v-if="open === r.id" class="routine-runs">
              <p v-if="!runs[r.id]?.length" class="t-meta c-faint">还没有执行过</p>
              <ol v-else class="routine-runs__list">
                <li v-for="run in runs[r.id]" :key="run.id" class="routine-run">
                  <div class="routine-run__head t-meta">
                    <v-chip
                      size="x-small"
                      variant="tonal"
                      :color="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : undefined"
                    >
                      {{ RUN_LABEL[run.status] }}
                    </v-chip>
                    <span>{{ fmt(run.scheduled_for || run.created_at, r.timezone) }}</span>
                    <span v-if="run.finished_at" class="c-faint">结束于 {{ fmt(run.finished_at, r.timezone) }}</span>
                  </div>
                  <div class="t-meta c-muted">{{ run.trigger_detail }}</div>
                  <div v-if="run.status === 'failed' || run.status === 'skipped'" class="t-body c-danger">
                    {{ run.error || '没有给出原因' }}
                  </div>
                  <div v-else-if="run.summary" class="t-body">{{ run.summary }}</div>
                  <div v-if="run.outputs.length" class="t-meta">
                    结果：
                    <router-link
                      :to="{ name: 'workspace-topic', params: { projectId: r.project_id, topicId: r.topic_id } }"
                    >
                      {{ run.outputs.join('、') }}
                    </router-link>
                  </div>
                </li>
              </ol>
            </div>
          </li>
        </ul>

        <div v-if="!routines.length && !loadError" class="py-8 text-center">
          <p class="t-body c-muted">还没有定时或触发规则</p>
          <p class="t-meta c-faint mt-1">点「新建」，或在房间里让芝士帮你起草一条</p>
        </div>
      </template>
    </div>

    <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。 -->
    <AdaptiveDialog
      :model-value="!!editing"
      :title="editingTitle"
      primary-label="保存"
      :primary-loading="saving"
      :max-width="560"
      @update:model-value="editing = null"
      @primary="save"
    >
      <template v-if="editing">
        <v-select
          v-if="editing === 'new'"
          v-model="form.room"
          autocomplete="off"
          :items="rooms"
          item-title="title"
          item-value="id"
          label="在哪个房间执行"
          hint="执行者是这个房间里的 AI 队友，结果也放在这个房间"
          persistent-hint
          class="mb-3"
        />
        <v-text-field v-model="form.title" autocomplete="off" label="名称" placeholder="例如：每周项目进展" />
        <v-textarea
          v-model="form.instructions"
          autocomplete="off"
          label="要做的工作"
          rows="3"
          auto-grow
          placeholder="例如：汇总本周各房间完成的任务、进行中的事和阻碍，写成一页周报"
        />
        <v-text-field
          v-model="form.context_scope"
          autocomplete="off"
          label="使用哪些资料"
          placeholder="例如：本项目所有房间的任务和决策；只用资料库里的文件"
        />
        <v-text-field
          v-model="form.output_dir"
          autocomplete="off"
          label="结果放在房间的哪个目录"
          placeholder="例如：周报"
        />
        <v-select v-model="form.trigger" autocomplete="off" :items="TRIGGERS" label="什么时候开工" />
        <template v-if="form.trigger === 'schedule'">
          <div class="routine-form__row">
            <v-select v-model="form.freq" autocomplete="off" :items="FREQS" label="频率" />
            <v-text-field
              v-if="form.freq !== 'hourly'"
              v-model="form.time"
              autocomplete="off"
              type="time"
              label="时间"
            />
            <v-text-field
              v-else
              v-model.number="form.minute"
              autocomplete="off"
              type="number"
              min="0"
              max="59"
              label="第几分钟"
            />
            <v-text-field
              v-if="form.freq === 'monthly'"
              v-model.number="form.day"
              autocomplete="off"
              type="number"
              min="1"
              max="31"
              label="几号"
            />
          </div>
          <v-chip-group v-if="form.freq === 'weekly'" v-model="form.weekdays" multiple column class="mb-2">
            <v-chip v-for="(d, i) in WEEKDAYS" :key="d" :value="i" filter size="small">{{ d }}</v-chip>
          </v-chip-group>
          <v-text-field v-model="form.timezone" autocomplete="off" label="时区" />
        </template>
        <v-select
          v-else-if="form.trigger !== 'library_file_added'"
          v-model="form.scope"
          autocomplete="off"
          :items="[
            { value: 'room', title: '只看这个房间' },
            { value: 'project', title: '整个项目' },
          ]"
          label="范围"
        />
        <p class="t-meta c-faint">
          AI 队友的工作电脑在线才能开工：用云端的项目随时可以；用自有设备的项目，设备离线时这次执行会排队，
          两小时内没开始会记为失败并通知你
        </p>
        <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
      </template>
    </AdaptiveDialog>

    <v-dialog :model-value="!!confirmingDelete" max-width="420" @update:model-value="confirmingDelete = null">
      <v-card v-if="confirmingDelete">
        <v-card-title class="t-dialog-title">删除「{{ confirmingDelete.title }}」</v-card-title>
        <v-card-text class="t-body">删除后不再执行，执行记录也一起删除；已经放进房间的结果文件保留</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirmingDelete = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="remove(confirmingDelete)">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </AppPage>
</template>

<style scoped>
.routine-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
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
.routine-form__row {
  display: flex;
  gap: 8px;
}
/* 手机上三格并排每格只剩一百来像素，「频率」的下拉和时间都挤不下：竖着排。字段之间的
   空隙由每一格底下的 details 行给。 */
@media (max-width: 959.98px) {
  .routine-form__row {
    flex-direction: column;
    gap: 0;
  }
}
</style>
