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
import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'
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
      { key: 'edit', label: t('routines.action.edit'), icon: 'mdi-pencil-outline', onSelect: () => startEdit(r) },
      {
        key: 'drop',
        label: t('routines.action.drop'),
        icon: 'mdi-delete-outline',
        danger: true,
        onSelect: () => (confirmingDelete.value = r),
      },
    ]
  return [
    r.state === 'active'
      ? {
          key: 'pause',
          label: t('routines.action.pause'),
          icon: 'mdi-pause',
          loading: busy.value === `${r.id}:pause`,
          onSelect: () => void act(r, 'pause'),
        }
      : {
          key: 'resume',
          label: t('routines.action.resume'),
          icon: 'mdi-play',
          loading: busy.value === `${r.id}:resume`,
          onSelect: () => void act(r, 'resume'),
        },
    {
      key: 'run',
      label: t('routines.action.runNow'),
      icon: 'mdi-play-circle-outline',
      loading: busy.value === `${r.id}:run-now`,
      onSelect: () => void act(r, 'run-now'),
    },
    { key: 'edit', label: t('routines.action.edit'), icon: 'mdi-pencil-outline', onSelect: () => startEdit(r) },
    {
      key: 'runs',
      label: open.value === r.id ? t('routines.action.hideRuns') : t('routines.action.showRuns'),
      icon: 'mdi-history',
      onSelect: () => void toggle(r.id),
    },
    {
      key: 'delete',
      label: t('routines.action.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (confirmingDelete.value = r),
    },
  ]
}

// Index 0 is Monday, as the backend's `weekdays` counts. 2024-01-01 was a Monday.
const WEEKDAYS = computed(() => {
  const format = new Intl.DateTimeFormat(i18n.global.locale.value, { weekday: 'short', timeZone: 'UTC' })
  return Array.from({ length: 7 }, (_, i) => format.format(new Date(Date.UTC(2024, 0, 1 + i))))
})
const TRIGGERS = computed(() =>
  (['schedule', 'library_file_added', 'task_closed', 'card_accepted'] as const).map((value) => ({
    value,
    title: t(`routines.trigger.${value}`),
  }))
)
const FREQS = computed(() =>
  (['daily', 'weekly', 'monthly', 'hourly'] as const).map((value) => ({ value, title: t(`routines.freq.${value}`) }))
)
const stateLabel = (state: Routine['state']) => t(`routines.state.${state}`)
const runLabel = (status: RoutineRun['status']) => t(`routines.run.${status}`)

const roomTitle = (id: string) => rooms.value.find((r) => r.id === id)?.title ?? t('routines.goneRoom')
const drafts = computed(() => routines.value.filter((r) => r.state === 'draft'))
const others = computed(() => routines.value.filter((r) => r.state !== 'draft'))

// 按规则自己的时区写：「每周一 09:00（Asia/Shanghai）」旁边的下次时间要对得上。
function fmt(iso: string | null, timeZone?: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(i18n.global.locale.value, { timeZone, hour12: false })
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
    loadError.value = e instanceof Error ? e.message : t('routines.loadFailed')
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
    actionError.value = e instanceof Error ? e.message : t('routines.runsFailed')
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
    actionError.value = e instanceof Error ? e.message : t('routines.actionFailed')
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
    actionError.value = e instanceof Error ? e.message : t('routines.deleteFailed')
  } finally {
    busy.value = ''
  }
}

// ── 新建 / 编辑 ────────────────────────────────────────────────────────────
const editing = ref<Routine | 'new' | null>(null)
// 关上的那一下 editing 已经是 null，标题还要照着刚才那一条画完收起的动画。
const editingTitle = ref('')
watch(editing, (value) => {
  if (value)
    editingTitle.value = value === 'new' ? t('routines.newTitle') : t('routines.editTitle', { title: value.title })
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
      if (!form.room) throw new Error(t('routines.pickRoom'))
      const created = await createRoutine(form.room, body)
      routines.value = [...routines.value, created]
    } else if (editing.value) {
      const updated = await updateRoutine(editing.value.id, body)
      routines.value = routines.value.map((x) => (x.id === updated.id ? updated : x))
    }
    editing.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : t('routines.saveFailed')
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
  {
    id: 'routines.refresh',
    title: t('routines.action.refresh'),
    palette: false,
    icon: 'mdi-refresh',
    loading: loading.value,
    header: {},
    run: load,
  },
  {
    id: 'routines.new',
    title: t('routines.action.new'),
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
        {{ t('routines.intro') }}
      </p>

      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>
      <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

      <div
        v-if="loading && !routines.length"
        class="py-8 text-center"
        role="status"
        :aria-label="t('routines.loading')"
      >
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else>
        <section v-if="drafts.length" class="mb-6">
          <h2 class="t-section mb-2">{{ t('routines.waiting') }}</h2>
          <ul class="routine-list">
            <li v-for="r in drafts" :key="r.id" class="routine-row routine-row--draft" :data-routine="r.id">
              <div class="routine-row__head">
                <div class="routine-row__id">
                  <div class="t-body routine-row__title">{{ r.title }}</div>
                  <div class="t-meta c-faint">{{ r.trigger_text }} · {{ roomTitle(r.topic_id) }}</div>
                </div>
                <v-chip size="small" color="warning" variant="tonal">{{ stateLabel(r.state) }}</v-chip>
                <AdaptiveMenu v-if="!mdAndUp" :actions="rowActions(r)" :title="r.title">
                  <template #activator="{ props: menuProps }">
                    <v-btn
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="small"
                      variant="text"
                      color="on-surface-variant"
                      class="tap-target"
                      :aria-label="t('routines.action.more')"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
              <dl class="routine-row__spec t-meta">
                <dt>{{ t('routines.spec.work') }}</dt>
                <dd>{{ r.instructions }}</dd>
                <dt>{{ t('routines.spec.scope') }}</dt>
                <dd>{{ r.context_scope || t('routines.spec.unlimited') }}</dd>
                <dt>{{ t('routines.spec.output') }}</dt>
                <dd>{{ t('routines.spec.outputDir', { dir: r.output_dir || t('routines.spec.rootDir') }) }}</dd>
                <dt>{{ t('routines.spec.agent') }}</dt>
                <dd>
                  <i18n-t keypath="routines.spec.draftedBy" scope="global">
                    <template #agent><UserRef :handle="r.agent_handle" /></template>
                    <template #author><UserRef :handle="r.proposed_by" /></template>
                  </i18n-t>
                </dd>
              </dl>
              <div class="routine-row__actions">
                <v-btn
                  size="small"
                  color="primary"
                  variant="flat"
                  :loading="busy === `${r.id}:confirm`"
                  @click="act(r, 'confirm')"
                >
                  {{ t('routines.action.confirm') }}
                </v-btn>
                <template v-if="mdAndUp">
                  <v-btn size="small" variant="text" @click="startEdit(r)">{{ t('routines.action.edit') }}</v-btn>
                  <v-btn size="small" variant="text" color="on-surface-variant" @click="confirmingDelete = r">
                    {{ t('routines.action.drop') }}
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
                    · {{ t('routines.next', { time: fmt(r.next_run_at, r.timezone) }) }}
                  </template>
                </div>
              </div>
              <v-chip size="small" :color="r.state === 'active' ? 'success' : undefined" variant="tonal">
                {{ stateLabel(r.state) }}
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
                    :aria-label="t('routines.action.more')"
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
                {{ t('routines.action.pause') }}
              </v-btn>
              <v-btn v-else size="small" variant="text" :loading="busy === `${r.id}:resume`" @click="act(r, 'resume')">
                {{ t('routines.action.resume') }}
              </v-btn>
              <v-btn size="small" variant="text" :loading="busy === `${r.id}:run-now`" @click="act(r, 'run-now')">
                {{ t('routines.action.runNow') }}
              </v-btn>
              <v-btn size="small" variant="text" @click="startEdit(r)">{{ t('routines.action.edit') }}</v-btn>
              <v-btn size="small" variant="text" @click="toggle(r.id)">
                {{ open === r.id ? t('routines.action.hideRuns') : t('routines.action.showRuns') }}
              </v-btn>
              <v-btn size="small" variant="text" color="on-surface-variant" @click="confirmingDelete = r">{{
                t('routines.action.delete')
              }}</v-btn>
            </div>
            <div v-if="open === r.id" class="routine-runs">
              <p v-if="!runs[r.id]?.length" class="t-meta c-faint">{{ t('routines.neverRun') }}</p>
              <ol v-else class="routine-runs__list">
                <li v-for="run in runs[r.id]" :key="run.id" class="routine-run">
                  <div class="routine-run__head t-meta">
                    <v-chip
                      size="x-small"
                      variant="tonal"
                      :color="run.status === 'succeeded' ? 'success' : run.status === 'failed' ? 'error' : undefined"
                    >
                      {{ runLabel(run.status) }}
                    </v-chip>
                    <span>{{ fmt(run.scheduled_for || run.created_at, r.timezone) }}</span>
                    <span v-if="run.finished_at" class="c-faint">{{
                      t('routines.finishedAt', { time: fmt(run.finished_at, r.timezone) })
                    }}</span>
                  </div>
                  <div class="t-meta c-muted">{{ run.trigger_detail }}</div>
                  <div v-if="run.status === 'failed' || run.status === 'skipped'" class="t-body c-danger">
                    {{ run.error || t('routines.noReason') }}
                  </div>
                  <div v-else-if="run.summary" class="t-body">{{ run.summary }}</div>
                  <div v-if="run.outputs.length" class="t-meta">
                    {{ t('routines.outputs') }}
                    <router-link
                      :to="{ name: 'workspace-topic', params: { projectId: r.project_id, topicId: r.topic_id } }"
                    >
                      {{ run.outputs.join(t('routines.listSeparator')) }}
                    </router-link>
                  </div>
                </li>
              </ol>
            </div>
          </li>
        </ul>

        <div v-if="!routines.length && !loadError" class="py-8 text-center">
          <p class="t-body c-muted">{{ t('routines.empty') }}</p>
          <p class="t-meta c-faint mt-1">{{ t('routines.emptyHint') }}</p>
        </div>
      </template>
    </div>

    <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。 -->
    <AdaptiveDialog
      :model-value="!!editing"
      :title="editingTitle"
      :primary-label="t('global.save')"
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
          :label="t('routines.form.room')"
          :hint="t('routines.form.roomHint')"
          persistent-hint
          class="mb-3"
        />
        <v-text-field
          v-model="form.title"
          autocomplete="off"
          :label="t('routines.form.name')"
          :placeholder="t('routines.form.namePlaceholder')"
        />
        <v-textarea
          v-model="form.instructions"
          autocomplete="off"
          :label="t('routines.form.instructions')"
          rows="3"
          auto-grow
          :placeholder="t('routines.form.instructionsPlaceholder')"
        />
        <v-text-field
          v-model="form.context_scope"
          autocomplete="off"
          :label="t('routines.form.context')"
          :placeholder="t('routines.form.contextPlaceholder')"
        />
        <v-text-field
          v-model="form.output_dir"
          autocomplete="off"
          :label="t('routines.form.outputDir')"
          :placeholder="t('routines.form.outputDirPlaceholder')"
        />
        <v-select v-model="form.trigger" autocomplete="off" :items="TRIGGERS" :label="t('routines.form.trigger')" />
        <template v-if="form.trigger === 'schedule'">
          <div class="routine-form__row">
            <v-select v-model="form.freq" autocomplete="off" :items="FREQS" :label="t('routines.form.freq')" />
            <v-text-field
              v-if="form.freq !== 'hourly'"
              v-model="form.time"
              autocomplete="off"
              type="time"
              :label="t('routines.form.time')"
            />
            <v-text-field
              v-else
              v-model.number="form.minute"
              autocomplete="off"
              type="number"
              min="0"
              max="59"
              :label="t('routines.form.minute')"
            />
            <v-text-field
              v-if="form.freq === 'monthly'"
              v-model.number="form.day"
              autocomplete="off"
              type="number"
              min="1"
              max="31"
              :label="t('routines.form.day')"
            />
          </div>
          <v-chip-group v-if="form.freq === 'weekly'" v-model="form.weekdays" multiple column class="mb-2">
            <v-chip v-for="(d, i) in WEEKDAYS" :key="d" :value="i" filter size="small">{{ d }}</v-chip>
          </v-chip-group>
          <v-text-field v-model="form.timezone" autocomplete="off" :label="t('routines.form.timezone')" />
        </template>
        <v-select
          v-else-if="form.trigger !== 'library_file_added'"
          v-model="form.scope"
          autocomplete="off"
          :items="[
            { value: 'room', title: t('routines.form.scopeRoom') },
            { value: 'project', title: t('routines.form.scopeProject') },
          ]"
          :label="t('routines.form.scope')"
        />
        <p class="t-meta c-faint">
          {{ t('routines.form.machineNote') }}
        </p>
        <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
      </template>
    </AdaptiveDialog>

    <v-dialog :model-value="!!confirmingDelete" max-width="420" @update:model-value="confirmingDelete = null">
      <v-card v-if="confirmingDelete">
        <v-card-title class="t-dialog-title">{{
          t('routines.deleteTitle', { title: confirmingDelete.title })
        }}</v-card-title>
        <v-card-text class="t-body">{{ t('routines.deleteBody') }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirmingDelete = null">{{
            t('global.cancel')
          }}</v-btn>
          <v-btn variant="text" color="error" @click="remove(confirmingDelete)">{{
            t('routines.action.delete')
          }}</v-btn>
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
