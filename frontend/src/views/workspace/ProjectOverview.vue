<script setup lang="ts">
// 项目总览（容器）：读项目总览那份文档、最近进展、项目的任务和名册，去任务页、文档
// 页、全部任务都在这里；画面在同目录的 ProjectOverviewView。
import type { ArtifactApi } from '@/components/ArtifactManifest.vue'
import type { ProjectMemberRow, RoomTask } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import {
  deleteProjectArtifact,
  getProjectSite,
  listProjectArtifacts,
  mergeProjectArtifacts,
  publishProjectSite,
  renameProjectArtifact,
} from '@/api'
import { getDocumentText, getProjectOverview } from '@/api/projectDocuments'
import { listProjectProgress } from '@/api/projectProgress'
import { memberName } from '@/lib/agentNames'
import { readProjectTasks } from '@/lib/projectTasks'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

const overviewText = ref<string | null>(null)
// 还没读到的是 null：画面据此不写「0」和「暂无」。
const progress = ref<ProgressItem[] | null>(null)
const progressFailed = ref(false)
const tasks = ref<RoomTask[] | null>(null)

async function loadOverview() {
  const pid = props.projectId
  try {
    const { id } = await getProjectOverview(pid)
    const text = await getDocumentText(id)
    if (props.projectId === pid) overviewText.value = text
  } catch {
    // 读不到就按「还没写」显示：那一块的入口（写一份）仍然在。
    if (props.projectId === pid) overviewText.value = ''
  }
}
async function loadProgress() {
  const pid = props.projectId
  progressFailed.value = false
  try {
    const listed = await listProjectProgress(pid)
    if (props.projectId === pid) progress.value = listed.data
  } catch {
    if (props.projectId === pid) progressFailed.value = true
  }
}
async function loadTasks(maxAgeMs?: number) {
  const pid = props.projectId
  try {
    const listed = await readProjectTasks(pid, { maxAgeMs })
    if (props.projectId === pid) tasks.value = listed.data
  } catch {
    // 留着上一次的：一次网络抖动不该让「谁在做什么」变空。
  }
}
function loadAll() {
  void loadOverview()
  void loadProgress()
  void loadTasks()
}

// 切回这个标签页时补一次：看到的是此刻的项目，不是离开时的。
function onVisibility() {
  if (!document.hidden) {
    void loadProgress()
    void loadTasks(5_000)
  }
}
onMounted(() => {
  loadAll()
  document.addEventListener('visibilitychange', onVisibility)
})
onUnmounted(() => document.removeEventListener('visibilitychange', onVisibility))
watch(
  () => props.projectId,
  () => {
    overviewText.value = null
    progress.value = null
    tasks.value = null
    loadAll()
  }
)

const projectName = computed(() => store.projects.find((p) => p.id === props.projectId)?.name ?? '')
const members = computed(() => store.members as ProjectMemberRow[])
const names = computed(() =>
  Object.fromEntries(members.value.map((m) => [m.user_handle, memberName(m) || m.user_handle]))
)
// 没挑过头像（avatar_id 是 null）时给空串，交给 UserAvatar 画首字母。
const avatars = computed(() =>
  Object.fromEntries(members.value.map((m) => [m.user_handle, m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)]))
)

const artifactApi: ArtifactApi = {
  list: listProjectArtifacts,
  rename: renameProjectArtifact,
  merge: mergeProjectArtifacts,
  remove: deleteProjectArtifact,
  site: { read: getProjectSite, publish: publishProjectSite },
}

function openTask(task: { taskId: string; roomId: string }) {
  void router.push({
    name: 'workspace-task',
    params: { projectId: props.projectId, topicId: task.roomId, taskId: task.taskId },
  })
}
function openArtifact(artifactId: string) {
  void router.push({ name: 'project-artifact', params: { projectId: props.projectId, artifactId } })
}
function editOverview() {
  void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind: 'charter' } })
}
function allTasks() {
  void router.push({ name: 'project-tasks', params: { projectId: props.projectId } })
}
</script>

<template>
  <ProjectOverviewView
    :project-id="projectId"
    :project-name="projectName"
    :overview-text="overviewText"
    :progress="progress"
    :progress-failed="progressFailed"
    :tasks="tasks"
    :names="names"
    :avatars="avatars"
    :me="myHandle()"
    :artifact-api="artifactApi"
    @edit-overview="editOverview"
    @open-task="openTask"
    @open-artifact="openArtifact"
    @all-tasks="allTasks"
    @retry-progress="loadProgress"
  />
</template>
