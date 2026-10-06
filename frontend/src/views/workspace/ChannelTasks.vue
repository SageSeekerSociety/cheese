<script setup lang="ts">
// 一个频道的全部任务（容器）：读地址、取任务和名册、去任务页、新建任务都在这里，
// 画面在同目录的 ChannelTasksView。
import type { RoomTask } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { newTask } from '@/commands/topicActions'
import { memberName } from '@/lib/agentNames'
import { readProjectTasks } from '@/lib/projectTasks'
import { fetchTopicMembers } from '@/lib/topicPanelCache'
import { topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import ChannelTasksView from '@/views/workspace/ChannelTasksView.vue'

const props = defineProps<{ projectId: string; topicId: string }>()

const router = useRouter()
const store = useWorkspaceStore()

const tasks = ref<RoomTask[]>([])
const names = ref<Record<string, string>>({})
const loading = ref(true)
const failed = ref(false)

async function load() {
  failed.value = false
  try {
    tasks.value = (await readProjectTasks(props.projectId, { maxAgeMs: 2_000 })).data.filter(
      (task) => task.room_id === props.topicId
    )
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
async function loadNames() {
  try {
    const rows = (await fetchTopicMembers(props.topicId)).data
    names.value = Object.fromEntries(rows.map((m) => [m.member_handle, memberName(m) || m.member_handle]))
  } catch {
    // 名字取不到就显示 handle。
  }
}
onMounted(() => {
  void store.loadPlace(props.topicId)
  void load()
  void loadNames()
})
watch(
  () => [props.projectId, props.topicId],
  () => {
    loading.value = true
    void load()
    void loadNames()
  }
)

const channel = computed(() => store.placeById(props.topicId))

function openTask(task: RoomTask) {
  void router.push({
    name: 'workspace-task',
    params: { projectId: props.projectId, topicId: task.room_id, taskId: task.id },
  })
}
function create() {
  if (channel.value) void newTask(channel.value, router)
}
</script>

<template>
  <ChannelTasksView
    :channel-title="channel ? topicTitle(channel) : null"
    :tasks="tasks"
    :names="names"
    :me="myHandle()"
    :loading="loading"
    :failed="failed"
    @open-task="openTask"
    @new-task="create"
    @retry="load"
  />
</template>
