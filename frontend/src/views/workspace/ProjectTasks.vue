<script setup lang="ts">
// 全部任务（容器）：读地址里的频道筛选、取项目的任务和名册、去任务页、新建任务都在
// 这里，画面在同目录的 ProjectTasksView。频道下面那一行「全部任务」进来时地址上带着
// `?channel=`，筛选已经选好那个频道。
import type { ProjectMemberRow, RoomTask, Topic } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { newTask } from '@/commands/topicActions'
import { memberName } from '@/lib/agentNames'
import { liveTasks } from '@/lib/board'
import { readProjectTasks } from '@/lib/projectTasks'
import { topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectTasksView from '@/views/workspace/ProjectTasksView.vue'

const props = defineProps<{ projectId: string }>()

const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

const tasks = ref<RoomTask[]>([])
const loading = ref(true)
const failed = ref(false)

async function load() {
  const pid = props.projectId
  failed.value = false
  try {
    const listed = await readProjectTasks(pid, { maxAgeMs: 2_000 })
    if (props.projectId === pid) tasks.value = listed.data
  } catch {
    if (props.projectId === pid) failed.value = true
  } finally {
    if (props.projectId === pid) loading.value = false
  }
}
onMounted(load)
watch(
  () => props.projectId,
  () => {
    loading.value = true
    tasks.value = []
    void load()
  }
)

const channelId = computed(() => (typeof route.query.channel === 'string' ? route.query.channel : null))
function pickChannel(id: string | null) {
  const query = { ...route.query }
  if (id) query.channel = id
  else delete query.channel
  void router.replace({ query })
}

// 我看得见的频道。筛选菜单不列归档的，任务行上的频道名照样能查到它们。
const channels = computed(() =>
  (store.topics as Topic[]).map((topic) => ({
    id: topic.id,
    title: topicTitle(topic),
    archived: topic.status === 'archived',
  }))
)

// 归档频道里没走完的任务没人会再管，不列（`lib/board.liveTasks`）。
const live = computed(() => liveTasks(tasks.value, new Set(channels.value.filter((c) => c.archived).map((c) => c.id))))

const members = computed(() => store.members as ProjectMemberRow[])
const names = computed(() =>
  Object.fromEntries(members.value.map((m) => [m.user_handle, memberName(m) || m.user_handle]))
)
// 没挑过头像（avatar_id 是 null）时给空串，交给 UserAvatar 画首字母。
const avatars = computed(() =>
  Object.fromEntries(members.value.map((m) => [m.user_handle, m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)]))
)

function openTask(task: RoomTask) {
  void router.push({
    name: 'workspace-task',
    params: { projectId: props.projectId, topicId: task.room_id, taskId: task.id },
  })
}
// 新建任务建在筛选着的那个频道里；没选频道时建在「综合」。
function create() {
  const topics = store.topics as Topic[]
  const channel = (channelId.value && topics.find((topic) => topic.id === channelId.value)) || store.rootTopic
  if (channel) void newTask(channel, router)
}
</script>

<template>
  <ProjectTasksView
    :tasks="live"
    :channels="channels"
    :channel-id="channelId"
    :names="names"
    :avatars="avatars"
    :me="myHandle()"
    :loading="loading"
    :failed="failed"
    @open-task="openTask"
    @new-task="create"
    @retry="load"
    @pick-channel="pickChannel"
  />
</template>
