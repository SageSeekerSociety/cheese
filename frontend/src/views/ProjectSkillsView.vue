<script setup lang="ts">
// 技能：这个项目自己的技能，芝士整理的、手写的、导入的。保存过的那一版会带进之后每个
// 会话，所以芝士整理出来、或者改过的，都要人读一遍、点保存才算数。平台自带的不在这里：
// 那是平台写给芝士的说明，属于平台本身。
//
// 列表铺满，点一行从右边滑出详情；新建是手写，导入只给项目管理员。
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision, SkillImportPreview } from '../api/projectSkills'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { getProject } from '../api'
import {
  addProjectSkill,
  confirmProjectSkill,
  declineProjectSkill,
  deleteProjectSkill,
  getProjectSkill,
  listProjectSkills,
  previewSkillImport,
  restoreProjectSkill,
  updateProjectSkill,
} from '../api/projectSkills'

import { useCommands } from '@/commands'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import SkillDetailDrawer from '@/components/skills/SkillDetailDrawer.vue'
import SkillEditDialog from '@/components/skills/SkillEditDialog.vue'
import SkillImportDialog from '@/components/skills/SkillImportDialog.vue'
import i18n, { t } from '@/i18n'
import { useDialog } from '@/plugins/dialog'

/** 过了这个数，芝士不再主动提议新的（后端 `PROPOSAL_LIMIT`）；人加不拦，只提一句。 */
const CROWDED = 20

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const dialog = useDialog()

const skills = ref<ProjectSkill[]>([])
const loading = ref(false)
const loadError = ref('')
const canImport = ref(false)

const drafts = computed(() => skills.value.filter((s) => s.state === 'draft'))
const active = computed(() => skills.value.filter((s) => s.state === 'active'))

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(i18n.global.locale.value, { month: 'long', day: 'numeric' }) : ''
}

function originLabel(s: ProjectSkill): string {
  if (s.origin === 'import') return t('work.skills.list.originImport')
  if (s.origin === 'person') return t('work.skills.list.originPerson')
  return ''
}

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const [listed, project] = await Promise.all([listProjectSkills(projectId), getProject(projectId)])
    if (props.projectId !== projectId) return
    skills.value = listed.data
    canImport.value = project.can_manage_members === true
    const focus = typeof route.query.skill === 'string' ? route.query.skill : null
    if (focus && skills.value.some((s) => s.id === focus)) open(focus)
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

// ── 详情 ───────────────────────────────────────────────────────────────────
const selectedId = ref<string | null>(null)
const selected = computed(() => skills.value.find((s) => s.id === selectedId.value) ?? null)
const revisions = ref<ProjectSkillRevision[] | null>(null)
const busy = ref('')
const detailError = ref('')

function open(id: string) {
  selectedId.value = id
  revisions.value = null
  detailError.value = ''
}

function close() {
  selectedId.value = null
}

async function act(what: string, run: () => Promise<void>, failed: string) {
  busy.value = what
  detailError.value = ''
  try {
    await run()
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : failed
  } finally {
    busy.value = ''
  }
}

async function loadHistory() {
  const s = selected.value
  if (!s) return
  await act(
    'history',
    async () => {
      const { revisions: listed, ...fresh } = await getProjectSkill(s.id)
      replace(fresh)
      if (selectedId.value === s.id) revisions.value = listed
    },
    t('work.skills.historyFailed')
  )
}

function save() {
  const s = selected.value
  if (s) void act('save', async () => replace(await confirmProjectSkill(s.id)), t('work.skills.confirmFailed'))
}

// 芝士提议的新一份不要了：记成「拒绝过」而不是删掉，删了它下一次还会再提同一份。
function decline() {
  const s = selected.value
  if (!s) return
  void act(
    'decline',
    async () => {
      await declineProjectSkill(s.id)
      skills.value = skills.value.filter((x) => x.id !== s.id)
      close()
    },
    t('work.skills.deleteFailed')
  )
}

// 芝士改过的那一份不要了：回到正在用的那一版，改动作废。改动丢了找不回来，先确认
// （§3.7）：入口是灰的，红只出现在这一下确认上。
async function discard() {
  const s = selected.value
  if (!s) return
  const confirmed = await dialog
    .confirm(t('work.skills.discardHint'), {
      title: t('work.skills.discardTitle', { title: s.title }),
      confirmLabel: t('work.skills.discard'),
      danger: true,
    })
    .wait()
    .catch(() => false)
  if (!confirmed) return
  await act(
    'discard',
    async () => replace(await restoreProjectSkill(s.id, s.shipped_revision)),
    t('work.skills.discardFailed')
  )
}

function restore(revision: number) {
  const s = selected.value
  if (!s) return
  void act(
    `restore:${revision}`,
    async () => {
      await restoreProjectSkill(s.id, revision)
      const { revisions: listed, ...fresh } = await getProjectSkill(s.id)
      replace(fresh)
      revisions.value = listed
    },
    t('work.skills.restoreFailed')
  )
}

const confirmingDelete = ref<ProjectSkill | null>(null)
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

async function remove(s: ProjectSkill | null) {
  if (!s) return
  confirmingDelete.value = null
  await act(
    'delete',
    async () => {
      await deleteProjectSkill(s.id)
      skills.value = skills.value.filter((x) => x.id !== s.id)
      if (selectedId.value === s.id) close()
    },
    t('work.skills.deleteFailed')
  )
}

// ── 新建 / 修改 ────────────────────────────────────────────────────────────
const editing = ref<ProjectSkill | 'new' | null>(null)
const saving = ref(false)
const formError = ref('')
const taken = computed(() => skills.value.map((s) => s.name))

function startNew() {
  formError.value = ''
  editing.value = 'new'
}

function startEdit() {
  if (!selected.value) return
  formError.value = ''
  editing.value = selected.value
}

async function saveForm(value: ProjectSkillContent & { name: string }) {
  const target = editing.value
  if (!target) return
  saving.value = true
  formError.value = ''
  try {
    if (target === 'new') {
      const created = await addProjectSkill(props.projectId, value)
      skills.value = [...skills.value, created]
      open(created.id)
    } else {
      const { name: _name, ...content } = value
      replace(await updateProjectSkill(target.id, content))
      revisions.value = null
    }
    editing.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : t('work.skills.saveFailed')
  } finally {
    saving.value = false
  }
}

// ── 导入 ───────────────────────────────────────────────────────────────────
const importing = ref(false)
const preview = ref<SkillImportPreview | null>(null)
const reading = ref(false)
const adding = ref(false)
const importError = ref('')

function startImport() {
  preview.value = null
  importError.value = ''
  importing.value = true
}

async function readImport(source: { file: File } | { url: string }) {
  reading.value = true
  importError.value = ''
  try {
    preview.value = await previewSkillImport(props.projectId, source)
  } catch (e) {
    importError.value = e instanceof Error ? e.message : t('work.skills.import.readFailed')
  } finally {
    reading.value = false
  }
}

async function addImport(value: ProjectSkillContent & { name: string }) {
  adding.value = true
  importError.value = ''
  try {
    const created = await addProjectSkill(props.projectId, { ...value, imported: true })
    skills.value = [...skills.value, created]
    importing.value = false
    open(created.id)
  } catch (e) {
    importError.value = e instanceof Error ? e.message : t('work.skills.import.addFailed')
  } finally {
    adding.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    skills.value = []
    close()
    void load()
  },
  { immediate: true }
)

// 页头：手机上「新建」是顶栏那一颗，「导入」「刷新」进 ⋯。
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
  ...(canImport.value
    ? [
        {
          id: 'skills.import',
          title: t('work.skills.import.title'),
          icon: 'mdi-download-outline',
          header: {},
          run: startImport,
        },
      ]
    : []),
  {
    id: 'skills.new',
    title: t('work.skills.create'),
    icon: 'mdi-plus',
    header: { primary: true, accent: true },
    run: startNew,
  },
])
</script>

<template>
  <AppPage :title="t('navigation.project.skills')">
    <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>

    <div v-if="loading && !skills.length" class="py-8 text-center" role="status" :aria-label="t('work.skills.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <section v-if="drafts.length" class="skills-group">
        <h2 class="t-eyebrow c-muted skills-group__head">
          {{ t('work.skills.awaiting') }}<span class="c-faint skills-group__count">{{ drafts.length }}</span>
        </h2>
        <ul class="skills-list">
          <!-- 整行可点；键盘落在名称那颗按钮上。行里提到的人是链接，点它不开详情。 -->
          <li
            v-for="s in drafts"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="open(s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="open(s.id)">{{ s.title }}</button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <i18n-t
                :keypath="s.shipped_revision ? 'work.skills.list.draftEdit' : 'work.skills.list.draftNew'"
                tag="span"
              >
                <template #name><UserRef :handle="s.proposed_by" /></template>
              </i18n-t>
              <span>{{ fmt(s.updated_at) }}</span>
            </span>
          </li>
        </ul>
      </section>

      <section v-if="active.length" class="skills-group">
        <h2 class="t-eyebrow c-muted skills-group__head">
          {{ t('work.skills.mine') }}<span class="c-faint skills-group__count">{{ active.length }}</span>
          <span v-if="active.length > CROWDED" class="t-meta c-muted skills-group__note">
            {{ t('work.skills.crowded') }}
          </span>
        </h2>
        <ul class="skills-list">
          <li
            v-for="s in active"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="open(s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="open(s.id)">{{ s.title }}</button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <span v-if="s.origin === 'cheese'">
                <i18n-t keypath="work.skills.list.originCheese" tag="span">
                  <template #name><UserRef :handle="s.proposed_by" /></template>
                </i18n-t>
              </span>
              <span v-else>{{ originLabel(s) }}</span>
              <span>{{
                t('work.skills.list.revision', { revision: s.shipped_revision, date: fmt(s.confirmed_at) })
              }}</span>
            </span>
          </li>
        </ul>
      </section>

      <p v-if="!skills.length && !loadError" class="t-body c-muted py-8 text-center">{{ t('work.skills.empty') }}</p>
    </template>

    <SkillDetailDrawer
      :skill="selected"
      :revisions="revisions"
      :busy="busy"
      :error="detailError"
      @close="close"
      @save="save"
      @decline="decline"
      @discard="discard"
      @edit="startEdit"
      @delete="confirmingDelete = selected"
      @history="loadHistory"
      @restore="restore"
    />

    <SkillEditDialog
      :editing="editing"
      :taken="taken"
      :saving="saving"
      :error="formError"
      @close="editing = null"
      @save="saveForm"
    />

    <SkillImportDialog
      :open="importing"
      :preview="preview"
      :reading="reading"
      :adding="adding"
      :error="importError"
      @close="importing = false"
      @read="readImport"
      @back="preview = null"
      @add="addImport"
    />

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
.skills-group + .skills-group {
  margin-top: 24px;
}

.skills-group__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 8px;
}

.skills-group__note {
  font-weight: 400;
}

.skills-list {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}

.skills-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.skills-row:hover,
.skills-row.is-selected {
  background: var(--fill);
}

.skills-row__title {
  padding: 0;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.skills-row__title:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.skills-row__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.skills-row__title {
  align-self: flex-start;
  color: var(--ink);
}

.skills-row__use {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skills-row__meta {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  align-items: flex-end;
  text-align: right;
}

@media (max-width: 599px) {
  .skills-row {
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
  }

  .skills-row__meta {
    flex-direction: row;
    gap: 8px;
    align-items: baseline;
  }
}
</style>
