<script setup lang="ts">
// 全部任务（容器）：读地址里的频道筛选、取项目的任务和名册、去任务页、新建任务都在
// 这里，画面在同目录的 ProjectTasksView。频道下面那一行「全部任务」进来时地址上带着
// `?channel=`，筛选已经选好那个频道。
//
// 进行中的整份读（和侧栏、项目总览同一份）；已关闭的和已归档的只会越积越多，各自一页一页
// 读，换标签、换筛选就从第一页重读。
import type { TaskFilter, TaskTab } from '@/api/tasks'
import type { ProjectMemberRow, RoomTask, Topic } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { newTask } from '@/commands/topicActions'
import { memberName } from '@/lib/agentNames'
import { liveTasks } from '@/lib/board'
import { channelGlyph, topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { openProjectTasksQuery, pagedProjectTasksQuery } from '@/query/tasks'
import { useWorkspaceStore } from '@/stores/workspace'
import ProjectTasksView from '@/views/workspace/ProjectTasksView.vue'

const props = defineProps<{ projectId: string }>()

const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()

// 和侧栏、项目总览同一份：刚读过就直接画，任务变了（房间里的通知）就重读。
const openRead = useQuery(
  computed(() => openProjectTasksQuery(props.projectId)),
  queryClient
)
const tasks = computed<RoomTask[]>(() => openRead.data.value?.data ?? [])
const loading = computed(() => openRead.isPending.value && !openRead.isError.value)

const channelId = computed(() => (typeof route.query.channel === 'string' ? route.query.channel : null))

// ---- 已关闭的、已归档的，各自一页一页 ----
const tab = ref<TaskTab>('open')
const whose = ref<TaskFilter>('all')
// 一页一页读的那两份共用一次查询：切标签时换成另一份（key 里带着状态，两份互不干扰），
// 「进行中」那一份不从这里读。
const pagedStatus = computed<'closed' | 'archived' | null>(() => (tab.value === 'open' ? null : tab.value))
const pagedRead = useInfiniteQuery(
  computed(() => ({
    ...pagedProjectTasksQuery(props.projectId, pagedStatus.value ?? 'closed', {
      channel: channelId.value,
      whose: whose.value,
    }),
    enabled: pagedStatus.value !== null,
  })),
  queryClient
)
const pagedTasks = computed<RoomTask[]>(() => pagedRead.data.value?.pages.flatMap((page) => page.data) ?? [])
const pagedCounts = computed<Record<TaskFilter, number> | null>(() => pagedRead.data.value?.pages[0]?.counts ?? null)
const pagedHasMore = computed(() => pagedRead.hasNextPage.value)
const pagedLoading = computed(() => pagedRead.isFetching.value)
const pagedFailed = computed(() => pagedRead.isError.value)

// 401/403 不是「没读出来」：登录没了，或者人已经不在这个项目里。重试换不来别的答案，
// 交给整页那一屏（ProjectAccessNotice），它给的是去登录的路。
const denied = (e: Error | null) => !!e && store.noteAccess(e)
watch(() => openRead.error.value, denied)
watch(() => pagedRead.error.value, denied)
const failed = computed(() => openRead.isError.value && !store.accessDenied)

// 下一页没读成时，「重试」就是再读一次这一页。
function morePaged() {
  if (pagedRead.isFetchingNextPage.value) return
  if (pagedRead.isError.value && !pagedRead.data.value) void pagedRead.refetch()
  else void pagedRead.fetchNextPage()
}
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
    glyph: channelGlyph(topic),
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
    v-model:tab="tab"
    v-model:whose="whose"
    :tasks="live"
    :channels="channels"
    :channel-id="channelId"
    :names="names"
    :avatars="avatars"
    :me="myHandle()"
    :loading="tab === 'open' ? loading : pagedCounts === null && !pagedFailed"
    :failed="failed || (pagedStatus !== null && pagedFailed && !pagedTasks.length && !store.accessDenied)"
    :paged-tasks="pagedTasks"
    :paged-counts="pagedCounts"
    :paged-has-more="pagedHasMore"
    :paged-loading="pagedLoading"
    :paged-failed="pagedFailed"
    @open-task="openTask"
    @new-task="create"
    @retry="pagedStatus !== null ? morePaged() : openRead.refetch()"
    @pick-channel="pickChannel"
    @more="morePaged"
  />
</template>
