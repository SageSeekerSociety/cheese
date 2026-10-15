<script setup lang="ts">
import type { WaitingItem } from '@/cx_types'

import { computed, onMounted, ref } from 'vue'

import { useJoinSpace } from '@/composables/useJoinSpace'
import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listAwaitingMe, markAllAlertsRead, markRead, resolveAlert } from '@/api'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { useNotificationAnswers } from '@/views/home/useNotificationAnswers'
import { useNotificationFeed } from '@/views/home/useNotificationFeed'
import InboxViewView from '@/views/InboxViewView.vue'

// 「待办」：首页那一格点开就是这一页（手机上是底栏的一格）。
//
// 上面是**等你处理**：「需要我处理」只在这一处。和任务列表读同一份规则（后端
// `room_task/presentation.py`），范围换成我能看见的全部项目，再按「这件事点的是谁」过滤。
// 它答的是「现在还没处理完的有哪些」，处理完就消失。按项目分组；每一件先说要你做什么，
// 再说是哪件事、等的是什么（提问的原话、改动的主题、停住的原因）。要你拍板的那几件，
// 选项就摆在这一行上；芝士写给你的变更提醒读过就点「标为已读」。
//
// 下面是**动态**：提到你、回复你、邀请你、截止提醒。它们是一条条事件，读过就算。以前
// 它们在顶栏的铃铛里；铃铛拆了，两样东西放在同一页，人回来只看这一处。
//
// 这一件是**容器**：整个画面连同下面的动态和那个加入空间的小对话框都交给
// `InboxViewView.vue` 就地画出来，这里只留要动后端和 store 的那部分——读「等你处理」
// 的清单、拍板 / 标已读 / 整项目清、新建项目的对话框、用邀请码加入空间，动态那一块的
// 取数与偏好（`views/home/useNotificationFeed.ts`），以及行里那三类邀请 / 申请答一下
// 的请求与状态（`views/home/useNotificationAnswers.ts`）——再把这串状态递进去、把两边
// 发上来的动作接住。行里答邀请的桥就由这里交给动态，往后一路传到那一行。
defineOptions({ name: 'InboxView' })

const items = ref<WaitingItem[]>([])
const loading = ref(true)
const failed = ref(false)
const errorReason = ref('')

async function load() {
  loading.value = true
  failed.value = false
  errorReason.value = ''
  try {
    items.value = (await listAwaitingMe()).data
  } catch (e) {
    failed.value = true
    errorReason.value = e instanceof Error ? e.message : ''
  } finally {
    loading.value = false
  }
}

const store = useWorkspaceStore()
onMounted(() => {
  void load()
  if (!store.projectsSettled) void store.refreshProjects()
})

// 一个项目都没有的人（刚注册）：这一页给他唯一有意义的两步。
const noProjects = computed(() => store.projectsSettled && store.projects.length === 0)
const { show: showNewProjectDialog } = useNewProjectDialog()
// 加入空间这件事和首页目录里那一行共用一份判断（见 `useJoinSpace`）：成了直接进那个空间。
const { open: joinOpen, busy: joinBusy, error: joinError, join: joinSpace } = useJoinSpace()

// 拍板、标为已读：交上去，这一件随之从清单上消失。
const acting = ref<number | null>(null)
const actionError = ref('')
async function settle(item: WaitingItem, send: (alertId: number) => Promise<unknown>) {
  if (item.alertId == null) return
  acting.value = item.alertId
  actionError.value = ''
  try {
    await send(item.alertId)
    items.value = items.value.filter((other) => other.alertId !== item.alertId)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
  } finally {
    acting.value = null
  }
}
const decide = (item: WaitingItem, chosen: string) => settle(item, (id) => resolveAlert(id, chosen))
const dismiss = (item: WaitingItem) => settle(item, markRead)

// 一个项目里攒了好几条变更提醒时，一下全部标为已读，不用一条一条点。
const clearing = ref<string | null>(null)
async function dismissAll(projectId: string) {
  clearing.value = projectId
  actionError.value = ''
  try {
    await markAllAlertsRead(projectId)
    items.value = items.value.filter((item) => item.projectId !== projectId || item.reason !== 'read')
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
  } finally {
    clearing.value = null
  }
}

// 「动态」那一块的取数、三档偏好、翻页和「来了新的」都在这里，画面只收结果。
const {
  filter,
  counts,
  notifications,
  loading: feedLoading,
  failed: feedFailed,
  moreFailed: feedMoreFailed,
  errorReason: feedErrorReason,
  hasMore: feedHasMore,
  fresh: feedFresh,
  reload: reloadFeed,
  loadMore: loadMoreFeed,
  markAsRead: markFeedRead,
  markAllAsRead: markAllFeedRead,
  deleteNotification,
  showFresh,
} = useNotificationFeed()

// 行里那三类邀请 / 申请（项目邀请、团队邀请、加入申请）答一下的动作与状态：动态只收
// 这一份桥，往后传给那一行，行自己不去取数。
const { answering } = useNotificationAnswers()
</script>

<template>
  <InboxViewView
    :no-projects="noProjects"
    :loading="loading"
    :failed="failed"
    :error-reason="errorReason"
    :items="items"
    :action-error="actionError"
    :acting="acting"
    :clearing="clearing"
    :feed-filter="filter"
    :feed-counts="counts"
    :feed-notifications="notifications"
    :feed-loading="feedLoading"
    :feed-failed="feedFailed"
    :feed-more-failed="feedMoreFailed"
    :feed-error-reason="feedErrorReason"
    :feed-has-more="feedHasMore"
    :feed-fresh="feedFresh"
    :answering="answering"
    :join-open="joinOpen"
    :join-busy="joinBusy"
    :join-error="joinError"
    @reload="load"
    @decide="decide"
    @answer="decide"
    @dismiss="dismiss"
    @dismiss-all="dismissAll"
    @new-project="showNewProjectDialog"
    @join="joinOpen = true"
    @update:feed-filter="filter = $event"
    @feed-mark-all-read="markAllFeedRead"
    @feed-mark-read="markFeedRead"
    @feed-delete="deleteNotification"
    @feed-more="loadMoreFeed"
    @feed-retry="reloadFeed"
    @feed-show-fresh="showFresh"
    @update:join-open="joinOpen = $event"
    @join-submit="joinSpace"
  />
</template>
