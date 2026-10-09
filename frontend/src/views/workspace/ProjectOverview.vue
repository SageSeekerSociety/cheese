<script setup lang="ts">
// 项目总览（容器）：读项目总览那份文档、最近进展、项目的任务和名册，去任务页、文档
// 页、全部任务都在这里；画面在同目录的 ProjectOverviewView。
import type { ArtifactApi } from '@/components/ArtifactManifest.vue'
import type { ProjectMemberRow, RoomTask } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { useCachedResource } from '@/composables/useCachedResource'

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
import { prefetchNow } from '@/lib/routePrefetch'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

// 三块各记着上一次读到的那份（`useCachedResource`）：回到这个项目的总览先照着它画，
// 背后重取，回来了原地换——不先清空成一片骨架。没读到过的才是 null，画面据此不写
// 「0」和「暂无」。
const overview = useCachedResource(
  () => `project-overview:${props.projectId}`,
  async (key) => {
    try {
      const { id } = await getProjectOverview(key.slice('project-overview:'.length))
      return await getDocumentText(id)
    } catch {
      // 读不到就按「还没写」显示：那一块的入口（写一份）仍然在。
      return ''
    }
  }
)
const recent = useCachedResource(
  () => `project-progress:${props.projectId}`,
  async (key) => {
    try {
      return (await listProjectProgress(key.slice('project-progress:'.length))).data
    } catch (e) {
      // 401/403 交给整页那一屏（ProjectAccessNotice）：重试换不来别的答案。
      store.noteAccess(e)
      throw e
    }
  }
)
// 一次网络抖动不该让「谁在做什么」变空：读失败时留着上一次的。
const work = useCachedResource(
  () => `project-tasks:${props.projectId}`,
  async (key) => (await readProjectTasks(key.slice('project-tasks:'.length), { maxAgeMs: 2_000 })).data
)
const overviewText = computed(() => overview.data.value ?? null)
const progress = computed<ProgressItem[] | null>(() => recent.data.value ?? null)
const progressFailed = computed(() => !!recent.error.value && !store.accessDenied)
const tasks = computed<RoomTask[] | null>(() => work.data.value ?? null)
function loadProgress() {
  void recent.refresh()
}

// 切回这个标签页时补一次：看到的是此刻的项目，不是离开时的。
function onVisibility() {
  if (!document.hidden) {
    void recent.refresh()
    void work.refresh()
  }
}
onMounted(() => document.addEventListener('visibilitychange', onVisibility))
onUnmounted(() => document.removeEventListener('visibilitychange', onVisibility))

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
// 这一行按下去（还没松开）就先把任务页的代码下下来；落点和 openTask 一直是同一个。
function pressTask(task: { taskId: string; roomId: string }) {
  prefetchNow({
    router,
    to: { name: 'workspace-task', params: { projectId: props.projectId, topicId: task.roomId, taskId: task.taskId } },
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
    @press-task="pressTask"
    @open-artifact="openArtifact"
    @all-tasks="allTasks"
    @retry-progress="loadProgress"
  />
</template>
