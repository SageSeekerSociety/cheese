<script setup lang="ts">
// 工作方法：这个项目存下来的做法。确认过的那一版会带进之后每个房间的 AI 队友，所以
// 芝士整理出来、或者改过的，都要人在这里读一遍、点确认才算数。
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision } from '../api/projectSkills'
import type { Topic } from '../cx_types'

import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'

import { listTopics } from '../api'
import {
  confirmProjectSkill,
  createProjectSkill,
  declineProjectSkill,
  deleteProjectSkill,
  getProjectSkill,
  listProjectSkills,
  restoreProjectSkill,
  updateProjectSkill,
} from '../api/projectSkills'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'
import { focusRow } from '@/lib/focusRow'
import { useDialog } from '@/plugins/dialog'

const props = defineProps<{ projectId: string }>()
const route = useRoute()

const skills = ref<ProjectSkill[]>([])
const rooms = ref<Topic[]>([])
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const busy = ref('')
const history = ref<{ skill: ProjectSkill; revisions: ProjectSkillRevision[] } | null>(null)
const viewing = ref<ProjectSkillRevision | null>(null)
const confirmingDelete = ref<ProjectSkill | null>(null)
const dialog = useDialog()
// 删除确认框的开关跟着「选中的那一条」走：有目标就是开着，关掉就把目标清掉。
const deleteOpen = computed({
  get: () => confirmingDelete.value !== null,
  set: (value) => {
    if (!value) confirmingDelete.value = null
  },
})
// 没有目标时不给标题，省得弹窗还没开就渲染出「删除「undefined」」。
const deleteTitle = computed(() =>
  confirmingDelete.value ? t('work.skills.deleteTitle', { title: confirmingDelete.value.title }) : ''
)

// 手机上一行的操作收进行首的 ⋯（底部面板）。等你确认的那一条，「确认保存」仍然摆在
// 行里——那是这一行唯一要紧的事。
const { mdAndUp } = useDisplay()
function draftActions(s: ProjectSkill): MenuAction[] {
  const edit: MenuAction = {
    key: 'edit',
    label: t('work.skills.edit'),
    icon: 'mdi-pencil-outline',
    onSelect: () => startEdit(s),
  }
  if (s.shipped_revision)
    return [
      edit,
      {
        key: 'discard',
        label: t('work.skills.discard'),
        icon: 'mdi-undo',
        loading: busy.value === `${s.id}:discard`,
        onSelect: () => void discard(s),
      },
    ]
  return [
    edit,
    {
      key: 'drop',
      label: t('work.skills.drop'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (confirmingDelete.value = s),
    },
  ]
}
function activeActions(s: ProjectSkill): MenuAction[] {
  return [
    { key: 'edit', label: t('work.skills.edit'), icon: 'mdi-pencil-outline', onSelect: () => startEdit(s) },
    { key: 'history', label: t('work.skills.history'), icon: 'mdi-history', onSelect: () => void openHistory(s) },
    {
      key: 'delete',
      label: t('work.skills.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (confirmingDelete.value = s),
    },
  ]
}

const drafts = computed(() => skills.value.filter((s) => s.state === 'draft'))
const active = computed(() => skills.value.filter((s) => s.state === 'active'))

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(i18n.global.locale.value, { hour12: false }) : '—'
}

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const [listed, topics] = await Promise.all([listProjectSkills(projectId), listTopics(projectId)])
    if (props.projectId !== projectId) return
    skills.value = listed.data
    rooms.value = topics.data.filter((tp) => tp.status !== 'archived')
    loading.value = false
    const focus = typeof route.query.skill === 'string' ? route.query.skill : null
    if (focus) void focusRow(`[data-skill="${CSS.escape(focus)}"]`)
  } catch (e) {
    if (props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : t('work.skills.loadFailed')
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

function replace(row: ProjectSkill) {
  skills.value = skills.value.map((s) => (s.id === row.id ? row : s))
}

async function confirm(s: ProjectSkill) {
  busy.value = `${s.id}:confirm`
  actionError.value = ''
  try {
    replace(await confirmProjectSkill(s.id))
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.skills.confirmFailed')
  } finally {
    busy.value = ''
  }
}

// 芝士改过的那一份不要了：回到正在用的那一版，改动作废。改动丢了找不回来，先确认
// （§3.7）：行里的入口是灰的，红只出现在这一下确认上。
async function discard(s: ProjectSkill) {
  const confirmed = await dialog
    .confirm(t('work.skills.discardHint'), {
      title: t('work.skills.discardTitle', { title: s.title }),
      confirmLabel: t('work.skills.discard'),
      danger: true,
    })
    .wait()
    .catch(() => false)
  if (!confirmed) return
  busy.value = `${s.id}:discard`
  actionError.value = ''
  try {
    replace(await restoreProjectSkill(s.id, s.shipped_revision))
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.skills.discardFailed')
  } finally {
    busy.value = ''
  }
}

async function openHistory(s: ProjectSkill) {
  actionError.value = ''
  try {
    const { revisions, ...fresh } = await getProjectSkill(s.id)
    replace(fresh)
    history.value = { skill: fresh, revisions }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.skills.historyFailed')
  }
}

async function restore(revision: number) {
  if (!history.value) return
  const s = history.value.skill
  busy.value = `${s.id}:restore:${revision}`
  try {
    await restoreProjectSkill(s.id, revision)
    const { revisions, ...fresh } = await getProjectSkill(s.id)
    replace(fresh)
    history.value = { skill: fresh, revisions }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.skills.restoreFailed')
  } finally {
    busy.value = ''
  }
}

async function remove(s: ProjectSkill | null) {
  if (!s) return
  confirmingDelete.value = null
  busy.value = `${s.id}:delete`
  actionError.value = ''
  try {
    // 芝士提议的新草稿记成「拒绝过」而不是删掉：删了它下一次还会再提同一份。
    if (s.proposal && !s.shipped_revision) await declineProjectSkill(s.id)
    else await deleteProjectSkill(s.id)
    skills.value = skills.value.filter((x) => x.id !== s.id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.skills.deleteFailed')
  } finally {
    busy.value = ''
  }
}

// ── 新建 / 编辑 ────────────────────────────────────────────────────────────
const editing = ref<ProjectSkill | 'new' | null>(null)
// 关上的那一下 editing 已经是 null，标题还要照着刚才那一条画完收起的动画。
const editingTitle = ref('')
watch(editing, (value) => {
  if (value)
    editingTitle.value =
      value === 'new' ? t('work.skills.newTitle') : t('work.skills.editTitle', { title: value.title })
})
const saving = ref(false)
const formError = ref('')
const form = reactive({
  room: '',
  name: '',
  title: '',
  description: '',
  inputs: '',
  steps: '',
  outputs: '',
  files: [] as { path: string; content: string }[],
})

function startNew() {
  Object.assign(form, {
    room: rooms.value[0]?.id ?? '',
    name: '',
    title: '',
    description: '',
    inputs: '',
    steps: '',
    outputs: '',
    files: [],
  })
  formError.value = ''
  editing.value = 'new'
}

function startEdit(s: ProjectSkill) {
  Object.assign(form, {
    room: '',
    name: s.name,
    title: s.title,
    description: s.description,
    inputs: s.inputs,
    steps: s.steps,
    outputs: s.outputs,
    files: Object.entries(s.files).map(([path, content]) => ({ path, content })),
  })
  formError.value = ''
  editing.value = s
}

function content(): ProjectSkillContent {
  return {
    title: form.title,
    description: form.description,
    inputs: form.inputs,
    steps: form.steps,
    outputs: form.outputs,
    files: Object.fromEntries(form.files.filter((f) => f.path.trim()).map((f) => [f.path.trim(), f.content])),
  }
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    if (editing.value === 'new') {
      if (!form.room) throw new Error(t('work.skills.roomRequired'))
      const created = await createProjectSkill(form.room, { name: form.name.trim(), ...content() })
      skills.value = [...skills.value, created]
    } else if (editing.value) {
      replace(await updateProjectSkill(editing.value.id, content()))
    }
    editing.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : t('work.skills.saveFailed')
  } finally {
    saving.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    skills.value = []
    history.value = null
    void load()
  },
  { immediate: true }
)
// 页头上的两件事：手机上「新建」是顶栏那一颗，「刷新」进 ⋯。
useCommands(() => [
  {
    id: 'skills.refresh',
    title: t('work.skills.refresh'),
    palette: false,
    icon: 'mdi-refresh',
    loading: loading.value,
    header: {},
    run: load,
  },
  {
    id: 'skills.new',
    title: t('work.skills.create'),
    icon: 'mdi-plus',
    disabled: !rooms.value.length,
    header: { primary: true, accent: true },
    run: startNew,
  },
])
</script>

<template>
  <AppPage :title="t('navigation.project.skills')">
    <div>
      <p class="t-body c-muted mb-6">
        {{ t('work.skills.intro') }}
      </p>

      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>
      <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

      <div
        v-if="loading && !skills.length"
        class="py-8 text-center"
        role="status"
        :aria-label="t('work.skills.loading')"
      >
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else>
        <section v-if="drafts.length" class="mb-6">
          <h2 class="t-section mb-2">{{ t('work.skills.awaiting') }}</h2>
          <ul class="skill-list">
            <li v-for="s in drafts" :key="s.id" class="skill-row skill-row--draft" :data-skill="s.id">
              <div class="skill-row__head">
                <div class="skill-row__id">
                  <div class="t-body skill-row__title">{{ s.title }}</div>
                  <div class="t-meta c-faint">
                    <i18n-t keypath="work.skills.proposedBy" tag="span">
                      <template #name>{{ s.name }}</template>
                      <template #user><UserRef :handle="s.proposed_by" /></template>
                    </i18n-t>
                    <template v-if="s.shipped_revision">
                      {{ t('work.skills.changeOf', { revision: s.shipped_revision }) }}
                    </template>
                  </div>
                </div>
                <v-chip size="small" color="warning" variant="tonal">{{ t('work.skills.pending') }}</v-chip>
                <AdaptiveMenu v-if="!mdAndUp" :actions="draftActions(s)" :title="s.title">
                  <template #activator="{ props: menuProps }">
                    <BaseButton
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="sm"
                      class="tap-target"
                      :aria-label="t('work.skills.more')"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
              <dl class="skill-row__spec t-meta">
                <dt>{{ t('work.skills.fields.description') }}</dt>
                <dd>{{ s.description }}</dd>
                <dt>{{ t('work.skills.fields.inputs') }}</dt>
                <dd>{{ s.inputs || '—' }}</dd>
                <dt>{{ t('work.skills.fields.steps') }}</dt>
                <dd>{{ s.steps }}</dd>
                <dt>{{ t('work.skills.fields.outputs') }}</dt>
                <dd>{{ s.outputs || '—' }}</dd>
                <template v-if="Object.keys(s.files).length">
                  <dt>{{ t('work.skills.fields.files') }}</dt>
                  <dd>{{ Object.keys(s.files).join(t('work.skills.listSeparator')) }}</dd>
                </template>
              </dl>
              <div class="skill-row__actions">
                <BaseButton kind="primary" size="sm" :loading="busy === `${s.id}:confirm`" @click="confirm(s)">
                  {{ t('work.skills.confirm') }}
                </BaseButton>
                <BaseButton v-if="mdAndUp" size="sm" @click="startEdit(s)">{{ t('work.skills.edit') }}</BaseButton>
                <BaseButton
                  v-if="mdAndUp && s.shipped_revision"
                  kind="ghost"
                  size="sm"
                  :loading="busy === `${s.id}:discard`"
                  @click="discard(s)"
                >
                  {{ t('work.skills.discard') }}
                </BaseButton>
                <BaseButton v-if="mdAndUp && !s.shipped_revision" size="sm" @click="confirmingDelete = s">
                  {{ t('work.skills.drop') }}
                </BaseButton>
              </div>
            </li>
          </ul>
        </section>

        <ul v-if="active.length" class="skill-list">
          <li v-for="s in active" :key="s.id" class="skill-row" :data-skill="s.id">
            <div class="skill-row__head">
              <div class="skill-row__id">
                <div class="t-body skill-row__title">{{ s.title }}</div>
                <div class="t-meta c-faint">
                  <i18n-t keypath="work.skills.confirmedBy" tag="span">
                    <template #name>{{ s.name }}</template>
                    <template #revision>{{ s.shipped_revision }}</template>
                    <template #user><UserRef :handle="s.confirmed_by" /></template>
                    <template #time>{{ fmt(s.confirmed_at) }}</template>
                  </i18n-t>
                </div>
                <div class="t-meta c-muted mt-1">{{ s.description }}</div>
              </div>
              <AdaptiveMenu v-if="!mdAndUp" :actions="activeActions(s)" :title="s.title">
                <template #activator="{ props: menuProps }">
                  <BaseButton
                    v-bind="menuProps"
                    icon="mdi-dots-horizontal"
                    size="sm"
                    class="tap-target"
                    :aria-label="t('work.skills.more')"
                  />
                </template>
              </AdaptiveMenu>
            </div>
            <div v-if="mdAndUp" class="skill-row__actions">
              <BaseButton size="sm" @click="startEdit(s)">{{ t('work.skills.edit') }}</BaseButton>
              <BaseButton size="sm" @click="openHistory(s)">{{ t('work.skills.history') }}</BaseButton>
              <BaseButton size="sm" @click="confirmingDelete = s">{{ t('work.skills.delete') }}</BaseButton>
            </div>
          </li>
        </ul>

        <div v-if="!skills.length && !loadError" class="py-8 text-center">
          <p class="t-body c-muted">{{ t('work.skills.empty') }}</p>
          <p class="t-meta c-faint mt-1">{{ t('work.skills.emptyHint') }}</p>
        </div>
      </template>
    </div>

    <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。 -->
    <AdaptiveDialog
      :model-value="!!editing"
      :title="editingTitle"
      :primary-label="t('work.skills.save')"
      :primary-loading="saving"
      size="lg"
      @update:model-value="editing = null"
      @primary="save"
    >
      <template v-if="editing">
        <template v-if="editing === 'new'">
          <v-select
            v-model="form.room"
            autocomplete="off"
            :items="rooms"
            item-title="title"
            item-value="id"
            :label="t('work.skills.form.room')"
          />
          <v-text-field
            v-model="form.name"
            autocomplete="off"
            :label="t('work.skills.form.name')"
            :placeholder="t('work.skills.form.namePlaceholder')"
          />
        </template>
        <v-text-field
          v-model="form.title"
          autocomplete="off"
          :label="t('work.skills.form.title')"
          :placeholder="t('work.skills.form.titlePlaceholder')"
        />
        <v-textarea
          v-model="form.description"
          autocomplete="off"
          :label="t('work.skills.form.description')"
          rows="2"
          auto-grow
        />
        <v-textarea
          v-model="form.inputs"
          autocomplete="off"
          :label="t('work.skills.fields.inputs')"
          rows="2"
          auto-grow
        />
        <v-textarea v-model="form.steps" autocomplete="off" :label="t('work.skills.fields.steps')" rows="5" auto-grow />
        <v-textarea
          v-model="form.outputs"
          autocomplete="off"
          :label="t('work.skills.fields.outputs')"
          rows="2"
          auto-grow
        />
        <div class="t-meta c-muted mb-2">{{ t('work.skills.form.files') }}</div>
        <div v-for="(f, i) in form.files" :key="i" class="skill-file">
          <v-text-field
            v-model="f.path"
            autocomplete="off"
            :label="t('work.skills.form.path')"
            placeholder="scripts/check.py"
          />
          <v-textarea
            v-model="f.content"
            autocomplete="off"
            :label="t('work.skills.form.content')"
            rows="3"
            auto-grow
            class="skill-file__body"
          />
          <BaseButton size="sm" @click="form.files.splice(i, 1)">{{ t('work.skills.form.removeFile') }}</BaseButton>
        </div>
        <BaseButton kind="secondary" size="sm" @click="form.files.push({ path: '', content: '' })">
          {{ t('work.skills.form.addFile') }}
        </BaseButton>
        <p v-if="formError" role="alert" class="t-body c-danger mt-2">{{ formError }}</p>
      </template>
    </AdaptiveDialog>

    <v-dialog :model-value="!!history" :max-width="DIALOG_WIDTH.lg" @update:model-value="history = null">
      <v-card v-if="history">
        <v-card-title class="t-dialog-title">{{
          t('work.skills.historyTitle', { title: history.skill.title })
        }}</v-card-title>
        <v-card-text>
          <ol class="skill-list">
            <li v-for="r in history.revisions" :key="r.revision" class="skill-row" :data-revision="r.revision">
              <div class="skill-row__head">
                <div class="skill-row__id t-meta">
                  <i18n-t keypath="work.skills.revisionLine" tag="span">
                    <template #revision>{{ r.revision }}</template>
                    <template #note>{{ r.note }}</template>
                    <template #user><UserRef :handle="r.confirmed_by" /></template>
                    <template #time>{{ fmt(r.created_at) }}</template>
                  </i18n-t>
                </div>
                <v-chip
                  v-if="r.revision === history.skill.shipped_revision"
                  size="x-small"
                  color="success"
                  variant="tonal"
                >
                  {{ t('work.skills.inUse') }}
                </v-chip>
              </div>
              <div class="skill-row__actions">
                <BaseButton size="sm" @click="viewing = viewing === r ? null : r">
                  {{ viewing === r ? t('work.skills.collapse') : t('work.skills.view') }}
                </BaseButton>
                <BaseButton
                  v-if="r.revision !== history.skill.shipped_revision"
                  size="sm"
                  :loading="busy === `${history.skill.id}:restore:${r.revision}`"
                  @click="restore(r.revision)"
                >
                  {{ t('work.skills.restore') }}
                </BaseButton>
              </div>
              <dl v-if="viewing === r" class="skill-row__spec t-meta">
                <dt>{{ t('work.skills.fields.description') }}</dt>
                <dd>{{ r.content.description }}</dd>
                <dt>{{ t('work.skills.fields.inputs') }}</dt>
                <dd>{{ r.content.inputs || '—' }}</dd>
                <dt>{{ t('work.skills.fields.steps') }}</dt>
                <dd>{{ r.content.steps }}</dd>
                <dt>{{ t('work.skills.fields.outputs') }}</dt>
                <dd>{{ r.content.outputs || '—' }}</dd>
              </dl>
            </li>
          </ol>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <BaseButton @click="history = null">{{ t('work.skills.close') }}</BaseButton>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <ConfirmDialog
      v-model="deleteOpen"
      :title="deleteTitle"
      :confirm-label="t('work.skills.delete')"
      danger
      @confirm="remove(confirmingDelete)"
    >
      {{ t('work.skills.deleteHint') }}
    </ConfirmDialog>
  </AppPage>
</template>

<style scoped>
.skill-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-row {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.skill-row.row--focus {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.skill-row--draft {
  border-color: rgb(var(--v-theme-warning));
}
.skill-row__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.skill-row__id {
  flex: 1;
  min-width: 0;
}
.skill-row__title {
  color: var(--text);
}
.skill-row__spec {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 12px;
  margin: 8px 0;
}
.skill-row__spec dt {
  color: var(--faint);
}
.skill-row__spec dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.skill-row__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}
.skill-file {
  border-left: 2px solid var(--line);
  padding-left: 8px;
  margin-bottom: 8px;
}
.skill-file__body :deep(textarea) {
  font-family: var(--font-mono, monospace);
}
</style>
