<script setup lang="ts">
// 资料库 › 项目文件的容器：取文件和改动记录、读写地址上的 `?file=`、跳去任务。画面在
// 同目录的 `ProjectFilesView.vue`。
//
// 项目当前版本里的文件（已采纳的那一版），整页铺开。
//
// 页头写「资料库 / 项目文件」，前一段点回资料库；旁边一行是最近一次改动，「改动记录」
// 看整个项目的。看哪一份记在地址上（`?file=`），刷新、把链接发给别人都回到这一份；
// 桌面上没点哪一份时右边是 README。手机上树和文件各占一页，← 从文件回到树。
//
// 以 `.` 开头的文件和文件夹（`.gitignore`、`.github/`）不列：那是仓库自己的配置，不是
// 项目里的东西。
import type { ProjectChange, WorkspaceFile } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { downloadFile, listFiles, listProjectChanges, projectFileBytes, workspaceFileRawUrl } from '@/api'
import { newTask } from '@/commands/topicActions'
import { useTopBarBack } from '@/components/common/topBarBack'
import { t } from '@/i18n'
import { usePageTitleStore } from '@/stores/title'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectFilesView from '@/views/library/ProjectFilesView.vue'

const props = defineProps<{ projectId: string }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const workspace = useWorkspaceStore()

function hidden(path: string): boolean {
  return path.split('/').some((part) => part.startsWith('.'))
}

// ---- 文件和最近一次改动 ---------------------------------------------------------

const files = ref<WorkspaceFile[]>([])
const loading = ref(true)
const loadError = ref('')
const latest = ref<ProjectChange | null>(null)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [listed, changes] = await Promise.all([
      listFiles(props.projectId, null, null, 'committed'),
      listProjectChanges(props.projectId, { limit: 1 }),
    ])
    files.value = listed.data.filter((f) => !hidden(f.path))
    latest.value = changes[0] ?? null
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : t('work.library.projectFiles.loadError')
  } finally {
    loading.value = false
  }
}
void load()

// ---- 看哪一份 ------------------------------------------------------------------

const filePath = computed(() => (typeof route.query.file === 'string' ? route.query.file : ''))
const readme = computed(() => files.value.find((f) => /^readme(\.[^/]*)?$/i.test(f.path)) ?? null)
const viewing = computed<WorkspaceFile | null>(
  () => files.value.find((f) => f.path === filePath.value) ?? (mdAndUp.value ? readme.value : null)
)

function select(path: string) {
  void router.push({ query: { ...route.query, file: path } })
}
function closeFile() {
  const next = { ...route.query }
  delete next.file
  void router.push({ query: next })
}

// 树默认收起，只摊开开着的那一份所在的那几层；人点开、收起的照人说的。
const opened = ref(new Set<string>())
watch(
  () => viewing.value?.path,
  (path) => {
    if (!path) return
    const parts = path.split('/')
    const next = new Set(opened.value)
    for (let i = 1; i < parts.length; i++) next.add(parts.slice(0, i).join('/'))
    opened.value = next
  },
  { immediate: true }
)
const collapsedDirs = computed(() => {
  const dirs = new Set<string>()
  for (const f of files.value) {
    const parts = f.path.split('/')
    for (let i = 1; i < parts.length; i++) {
      const dir = parts.slice(0, i).join('/')
      if (!opened.value.has(dir)) dirs.add(dir)
    }
  }
  return dirs
})
function toggleDir(path: string) {
  const next = new Set(opened.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  opened.value = next
}

const query = ref('')

// 开着的那一份最后一次是怎么改的。
const change = ref<ProjectChange | null>(null)
watch(
  () => viewing.value?.path,
  async (path) => {
    change.value = null
    if (!path) return
    try {
      const [found] = await listProjectChanges(props.projectId, { path, limit: 1 })
      if (viewing.value?.path === path) change.value = found ?? null
    } catch {
      // 读不到就不写这一行：文件照样能看。
    }
  },
  { immediate: true }
)

const read = computed(() => {
  const path = viewing.value?.path
  return path ? () => projectFileBytes(props.projectId, path) : null
})

function download() {
  const file = viewing.value
  if (!file) return
  void downloadFile(
    workspaceFileRawUrl(props.projectId, file.path, undefined, null, 'committed'),
    file.path.split('/').pop() ?? file.path
  )
}

// ---- 改动记录 ------------------------------------------------------------------

const historyOf = ref<{ path: string | null } | null>(null)
const history = ref<ProjectChange[]>([])
const historyLoading = ref(false)
const historyFailed = ref<string | null>(null)

async function loadHistory() {
  const target = historyOf.value
  if (!target) return
  historyLoading.value = true
  historyFailed.value = null
  try {
    history.value = await listProjectChanges(props.projectId, { path: target.path ?? undefined, limit: 50 })
  } catch (e) {
    historyFailed.value = e instanceof Error ? e.message : t('work.library.projectFiles.historyFailed')
  } finally {
    historyLoading.value = false
  }
}
function showHistory(path: string | null) {
  historyOf.value = { path }
  history.value = []
  void loadHistory()
}
const historyTitle = computed(() =>
  historyOf.value?.path
    ? t('work.library.projectFiles.fileHistoryTitle', { name: historyOf.value.path.split('/').pop() ?? '' })
    : t('work.library.projectFiles.historyTitle')
)

function openTask(taskId: string) {
  historyOf.value = null
  void router.push({ name: 'workspace-task', params: { projectId: props.projectId, taskId } })
}

function startTask() {
  const channel = workspace.rootTopic
  if (channel) void newTask(channel, router)
}

// ---- 手机上的顶栏 --------------------------------------------------------------

useTopBarBack(() =>
  !mdAndUp.value && filePath.value ? { label: t('work.library.projectFiles.backToTree'), onBack: closeFile } : null
)
const titles = usePageTitleStore()
watch(
  () =>
    !mdAndUp.value && viewing.value
      ? viewing.value.path.split('/').pop() ?? viewing.value.path
      : t('work.library.projectFiles.name'),
  (title) => titles.setDynamicTitle(title, 'project-library-files'),
  { immediate: true }
)
</script>

<template>
  <ProjectFilesView
    v-model:query="query"
    :project-id="projectId"
    :files="files"
    :loading="loading"
    :load-error="loadError"
    :latest="latest"
    :collapsed-dirs="collapsedDirs"
    :viewing="viewing"
    :change="change"
    :read="read"
    :wide="mdAndUp"
    :history-open="!!historyOf"
    :history-title="historyTitle"
    :history="history"
    :history-loading="historyLoading"
    :history-failed="historyFailed"
    @retry="load"
    @select="select"
    @toggle-dir="toggleDir"
    @show-history="showHistory"
    @download="download"
    @open-task="openTask"
    @new-task="startTask"
    @close-history="historyOf = null"
    @retry-history="loadHistory"
  />
</template>
