<script setup lang="ts">
// 项目总览（容器）：读项目总览那份文档、最近进展、项目的任务和名册，去任务页、文档
// 页、全部任务都在这里；画面在同目录的 ProjectOverviewView。
import type { ArtifactApi } from '@/components/ArtifactManifest.vue'
import type { ProjectMemberRow, RoomTask } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { holdRevealUntil } from '@/composables/useRevealGate'

import {
  deleteProjectArtifact,
  getProjectSite,
  listProjectArtifacts,
  mergeProjectArtifacts,
  publishProjectSite,
  renameProjectArtifact,
} from '@/api'
import { memberName } from '@/lib/agentNames'
import { prefetchNow } from '@/lib/routePrefetch'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { overviewQuery, progressQuery } from '@/query/project'
import { openProjectTasksQuery } from '@/query/tasks'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

// 三块都在缓存里：回到这个项目的总览先照着上一次的画，背后重取，回来了原地换——不先
// 清空成一片骨架；切回标签页时过期了的再取一次。没读到过的才是 null，画面据此不写
// 「0」和「暂无」。一次网络抖动不该让「谁在做什么」变空：读失败时留着上一次的。只要
// 还在进行的：总览画的是进行中的事。
const overview = useQuery(
  computed(() => overviewQuery(props.projectId)),
  queryClient
)
const recent = useQuery(
  computed(() => progressQuery(props.projectId)),
  queryClient
)
const work = useQuery(
  computed(() => openProjectTasksQuery(props.projectId)),
  queryClient
)
holdRevealUntil(() => !overview.isPending.value && !recent.isPending.value && !work.isPending.value)
// 401/403 交给整页那一屏（ProjectAccessNotice）：重试换不来别的答案。
watch(
  () => recent.error.value,
  (e) => {
    if (e) store.noteAccess(e)
  }
)
const overviewText = computed(() => overview.data.value?.text ?? null)
const progress = computed<ProgressItem[] | null>(() => recent.data.value ?? null)
const progressFailed = computed(() => recent.isError.value && !recent.data.value && !store.accessDenied)
const tasks = computed<RoomTask[] | null>(() => work.data.value?.data ?? null)
function loadProgress() {
  void recent.refetch()
}

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
