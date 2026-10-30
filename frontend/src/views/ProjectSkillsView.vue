<script setup lang="ts">
// 技能：这个项目自己的技能，芝士整理的、手写的、导入的。保存过的那一版会带进之后每个
// 会话，所以芝士整理出来、或者改过的，都要人读一遍、点保存才算数。平台自带的不在这里：
// 那是平台写给芝士的说明，属于平台本身。
//
// 列表铺满，点一行从右边滑出详情；新建是手写，导入只给项目管理员。
//
// 画的那一半在 `ProjectSkillsViewView.vue`：这里只留下请求、地址、页头那几颗命令，以及
// 删除确认（确认框挂在页面上，不在画面里）。
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision, SkillImportPreview } from '../api/projectSkills'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

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

import ProjectSkillsViewView from './ProjectSkillsViewView.vue'

import { useCommands } from '@/commands'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import { mayIn } from '@/lib/projectPermissions'
import { useDialog } from '@/plugins/dialog'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { projectQuery } from '@/query/project'
import { fromSnapshot } from '@/query/snapshot'

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const dialog = useDialog()

const skills = ref<ProjectSkill[]>([])
const loading = ref(false)
const loadError = ref('')
const canImport = ref(false)

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const [listed, project] = await Promise.all([
      fromSnapshot(keys.projectSkills(projectId), () => listProjectSkills(projectId)),
      queryClient.fetchQuery(projectQuery(projectId)),
    ])
    if (props.projectId !== projectId) return
    skills.value = listed.data
    canImport.value = mayIn(project, 'edit_settings')
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
// 打开那一份时读：配套文件的内容和历史版本，列表里不带。
const contents = ref<Record<string, string> | null>(null)
const revisions = ref<ProjectSkillRevision[] | null>(null)
const busy = ref('')
const detailError = ref('')

function open(id: string) {
  selectedId.value = id
  contents.value = null
  revisions.value = null
  detailError.value = ''
  void loadDetail()
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

async function loadDetail() {
  const id = selectedId.value
  if (!id) return
  try {
    const { revisions: listed, contents: files, ...fresh } = await getProjectSkill(id)
    if (selectedId.value !== id) return
    replace(fresh)
    contents.value = files
    revisions.value = listed
  } catch (e) {
    if (selectedId.value === id) detailError.value = e instanceof Error ? e.message : t('work.skills.loadFailed')
  }
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
    async () => {
      replace(await restoreProjectSkill(s.id, s.shipped_revision))
      await loadDetail()
    },
    t('work.skills.discardFailed')
  )
}

function restore(revision: number) {
  const s = selected.value
  if (!s) return
  void act(
    `restore:${revision}`,
    async () => {
      replace(await restoreProjectSkill(s.id, revision))
      await loadDetail()
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

function startNew() {
  formError.value = ''
  editing.value = 'new'
}

// 配套文件的内容还没读到时不能改：表单里文件是空的，一保存就把它们全删了。
function startEdit() {
  if (!selected.value || contents.value === null) return
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
      await loadDetail()
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
  <ProjectSkillsViewView
    v-model:editing="editing"
    v-model:importing="importing"
    :loading="loading"
    :load-error="loadError"
    :skills="skills"
    :selected-id="selectedId"
    :selected="selected"
    :contents="contents"
    :revisions="revisions"
    :busy="busy"
    :detail-error="detailError"
    :saving="saving"
    :form-error="formError"
    :preview="preview"
    :reading="reading"
    :adding="adding"
    :import-error="importError"
    @open="open"
    @close="close"
    @save="save"
    @decline="decline"
    @discard="discard"
    @start-edit="startEdit"
    @restore="restore"
    @delete="confirmingDelete = selected"
    @save-form="saveForm"
    @read-import="readImport"
    @add-import="addImport"
    @back="preview = null"
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
</template>
