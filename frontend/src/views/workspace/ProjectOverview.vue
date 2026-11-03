<script setup lang="ts">
// 项目总览（容器）：读项目目标、最近进展、项目的任务和名册，去任务页、文档
// 页、全部任务都在这里；画面在同目录的 ProjectOverviewView。
import type { ArtifactApi } from '@/components/ArtifactManifest.vue'
import type { ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { TaskStageStatus } from '@/types'
import type { ProgressItem } from '@/types/projectProgress'
import type { ChallengeDeadline, ChallengeStages, ProjectStarter } from '@/views/workspace/ProjectOverviewView.vue'

import { computed, ref, watch } from 'vue'
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
import { agentNames, memberName } from '@/lib/agentNames'
import { uploadLibraryFile } from '@/lib/libraryApi'
import { renderNoticeMessage } from '@/lib/noticeText'
import { requestDraft } from '@/lib/overviewDraft'
import { prefetchNow } from '@/lib/routePrefetch'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { TASK_FOLDER } from '@/lib/taskRevision'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { overviewQuery, progressQuery, projectQuery, situationQuery } from '@/query/project'
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
// 现状不挡整页：它最慢（可能正在后台写），读到了再出现在最上面。
const situation = useQuery(
  computed(() => situationQuery(props.projectId)),
  queryClient
)

// 从一道题领出来的项目：总览上方写这一份报名截到哪一刻。项目那一行里带着
// （`challenge_deadline`，后端按报名算），不是项目目标里写死的字 —— 那份是领取时写的，
// 那时还没批准，也不知道读的人在哪个时区。
const project = useQuery(
  computed(() => projectQuery(props.projectId)),
  queryClient
)
const challengeDeadline = computed<ChallengeDeadline | null>(() => {
  // 只在单个项目那一行（`GET /projects/{id}`）里有：报名批准时定下的截止（`mine`），
  // 还没有就是题目的截止。
  const d = project.data.value?.challenge_deadline as { at: string; mine: boolean } | null | undefined
  return d ? { at: Date.parse(d.at), mine: d.mine } : null
})
// 题目分了阶段：每个阶段的截止和这个项目在上面的状态，同样在那一行里（`challenge_stages`）。
const challengeStages = computed<ChallengeStages | null>(() => {
  const rows = project.data.value?.challenge_stages as
    | { name: string; description: string; due_at: string | null; status: TaskStageStatus }[]
    | undefined
  if (!rows?.length) return null
  return {
    stages: rows.map((r) => ({
      name: r.name,
      description: r.description,
      dueAt: r.due_at ? Date.parse(r.due_at) : null,
    })),
    statuses: rows.map((r) => r.status),
  }
})
// 代码仓库从题目的起始代码开始的：第几版、放好了没有，没放成的那句原因按读的人的语言写。
const starter = computed<ProjectStarter | null>(() => {
  const s = project.data.value?.starter
  if (!s) return null
  return { revision: s.revision, state: s.state, reason: renderNoticeMessage(s.error, '') }
})
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
// 「进行中的任务」那一个数要按后端那一个判据算，而不是自己拿列去凑（清单第 14 条）。
// 到现在为止两边碰巧一致：`closing` 的任务落的就是 `done` 列，所以「open 且不在 done
// 列」和 `underway_counts` 数目相同。但一致是碰巧，不是保证——所以数字从侧栏那几行
// 带下来的同一个字段里数，真正取数的地方只有后端一处。
const underway = computed(() => store.topics.reduce((sum, topic) => sum + (topic.my_tasks?.open ?? 0), 0))
function loadProgress() {
  void recent.refetch()
}

const projectName = computed(() => store.projects.find((p) => p.id === props.projectId)?.name ?? '')
const members = computed(() => store.members as ProjectMemberRow[])
const names = computed(() =>
  Object.fromEntries(members.value.map((m) => [m.user_handle, memberName(m) || m.user_handle]))
)
// 每个频道坐着的那位队友叫什么：任务没单独指定队友时，做它的就是它所在频道的这位。
const roomAgentNames = computed(() => {
  const seats = agentNames([], members.value)
  return Object.fromEntries(
    (store.topics as Topic[]).flatMap((topic) => {
      const name = topic.agent_handle ? seats.get(topic.agent_handle) : undefined
      return name ? [[topic.id, name]] : []
    })
  )
})
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
// 项目来自的那道题（`GET /projects/{id}` 的 `task`）：看的人看得到它的 `题目/` 才有。
const fromTask = computed(() => project.data.value?.task ?? null)
/** 起始代码没放成时那句下一步里的「题目」文件夹：项目资料库里的那一层。 */
function openTaskFolder() {
  void router.push({ name: 'project-library', params: { projectId: props.projectId }, query: { dir: TASK_FOLDER } })
}
function openTaskVersion(version: number) {
  const task = fromTask.value
  if (!task) return
  void router.push({
    name: TASK_ROUTE_NAMES.versions,
    params: { spaceId: String(task.space_id), taskId: String(task.id) },
    query: { version: String(version) },
  })
}
// 起草项目目标：材料先放进资料库（以后的人和 AI 队友都从那里读），再跳到项目目标那篇
// 文档，由那里的 AI 队友接着写（`lib/overviewDraft`）。
const drafting = ref(false)
const draftError = ref<string | null>(null)
async function draftOverview(draft: { note: string; files: File[] }) {
  const id = overview.data.value?.id
  if (!id || drafting.value) return
  drafting.value = true
  draftError.value = null
  try {
    const placed: string[] = []
    for (const file of draft.files) placed.push((await uploadLibraryFile(props.projectId, file)).path)
    const listed = placed.map((path) => `library/${path.replace(/^library\//, '')}`)
    const heading = '提问人放进资料库的材料（先读这几份）：' // i18n-data: 交给 AI 队友读的提示，不在界面上显示
    const files = listed.length ? `\n\n${heading}\n${listed.map((p) => `- ${p}`).join('\n')}` : ''
    requestDraft(id, `${draft.note}${files}`.trim())
    editOverview()
  } catch (error) {
    draftError.value = error instanceof Error ? error.message : String(error)
  } finally {
    drafting.value = false
  }
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
    :challenge-deadline="challengeDeadline"
    :challenge-stages="challengeStages"
    :starter="starter"
    :task-linked="fromTask !== null"
    :progress="progress"
    :progress-failed="progressFailed"
    :tasks="tasks"
    :underway="underway"
    :names="names"
    :room-agent-names="roomAgentNames"
    :avatars="avatars"
    :me="myHandle()"
    :artifact-api="artifactApi"
    :situation="situation.data.value ?? null"
    :agent-name="store.agentName"
    :agent-handle="store.agentHandle"
    :drafting="drafting"
    :draft-error="draftError"
    @draft-overview="draftOverview"
    @edit-overview="editOverview"
    @open-task="openTask"
    @press-task="pressTask"
    @open-artifact="openArtifact"
    @open-task-version="openTaskVersion"
    @open-task-folder="openTaskFolder"
    @all-tasks="allTasks"
    @retry-progress="loadProgress"
  />
</template>
