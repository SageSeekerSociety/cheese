// 这个地址说的是哪一页、看的是哪一段。
//
// 「你在看什么」进 URL（提案 A）：面板开着哪一格写在 `?tab=`，别人发来的「你来看一眼
// 这个 diff」才是一条能点开的链接。桌面上换页签是 replace，手机上 Back 先回到对话
// （`useRoomTabHistory`）。地址里点名的一条消息（搜索结果、别人发来的链接）要停在它
// 上面：支线页上点名的是支线里那一条，交给支线那一栏；频道主线那一栏里没有它。
//
// 已经写进提交历史的旧链接（`?card=<任务>`）：那一层下钻已经没有了，任务有自己的页面，
// 照着链接来的人直接送过去。
//
// 地址里这个 id 指向一个房间、一条支线，还是一个房间转成的任务，只有问过才知道。侧栏
// 那张表里找不到就直接问它——深链接、刷新，都走这条路。房间转成了任务时它留着原来的
// id，所以老链接、书签和「上次看到哪儿」记下的那个 id 都还指着它：这样来的 id 打开的是
// 它的任务页，而不是说这个房间没了。
import type { ComputedRef, Ref } from 'vue'
import type { Topic } from '@/cx_types'
import type { TopicPlace } from './shared'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useRoomTabHistory } from '@/composables/useRoomTabHistory'

import { getTask } from '@/api/tasks'
import { useTopBarBack } from '@/components/common/topBarBack'
import { t } from '@/i18n'
import { refRoute } from '@/lib/refDirectory'
import { readProjectTopic, usePlace } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'

export function useTopicAddress(opts: { place: () => TopicPlace; desktop: Ref<boolean> }) {
  const store = useWorkspaceStore()
  const router = useRouter()
  const route = useRoute()

  // 面板开着哪一格。
  const panelTab = computed(() => {
    const q = route.query.tab
    return typeof q === 'string' ? q : undefined
  })
  // 一句话里提到某条规则时，那个名字是个链接（`?routine=<id>`，芝士自己写在话里）：
  // 右栏切到「例行事务」，那一格等名单回来再选中这一条。和 `?tab=` 一样由地址说了
  // 算；人从旧消息点过来时名单常常还在路上，所以落上去的事交给那一格自己。
  const panelRoutineId = computed(() => {
    const q = route.query.routine
    return typeof q === 'string' && q ? q : null
  })
  const phone = computed(() => !opts.desktop.value)
  // 页签和浏览器历史怎么对应（桌面 replace；手机上 Back 先回到对话）。
  const tabHistory = useRoomTabHistory(phone)
  function onPanelTab(key: string) {
    tabHistory.goTab(key)
  }
  // 手机上不在对话那一格时，顶栏的 ← 和 Back 一样先回到对话。
  useTopBarBack(() =>
    phone.value && !tabHistory.onChat.value ? { label: t('work.room.backToChat'), onBack: tabHistory.toChat } : null
  )
  void tabHistory.ensureChatBehind()

  // 「去验收」：决策在聊天，审查在面板 —— 它不把人带去任何地方，只把右栏切到「改动」
  // 那一格。对话栏末尾那张验收卡和概览里那张卡上的按钮是同一个动作，所以只有这一处
  // 定义（`chatEvents.review` 和 `<WorkPanel @review>` 都指过来）。
  function onReview() {
    onPanelTab('changes')
  }

  // 地址点名的一条消息。
  const namedBlock = computed(() => {
    const q = route.query.block
    return typeof q === 'string' && q ? q : null
  })
  const focusBlock = computed(() => (opts.place().threadId ? null : namedBlock.value))
  const threadFocusBlock = computed(() => (opts.place().threadId ? namedBlock.value : null))

  watch(
    () => route.query.card,
    (card) => {
      if (typeof card !== 'string' || !card) return
      const { projectId, topicId } = opts.place()
      void router.replace({
        name: 'workspace-task',
        params: { projectId, topicId, taskId: card },
      })
    },
    { immediate: true }
  )

  function backToRoom() {
    const { projectId, topicId } = opts.place()
    void router.push({ name: 'workspace-topic', params: { projectId, topicId } })
  }
  function openTopic(topicId: string, blockId?: string) {
    if (topicId === opts.place().topicId) {
      if (blockId) jumpTo(blockId)
      return
    }
    void router.push(refRoute(opts.place().projectId, topicId, blockId))
  }
  function jumpTo(blockId: string) {
    void router.replace({ query: { ...route.query, block: blockId } })
  }

  // ---- 地址里这个 id 指向谁 ----
  const { place: selectedTopic, resolving: placeResolving } = usePlace(
    computed(() => store.projectId || opts.place().projectId),
    computed(() => opts.place().topicId),
    computed(() => store.topics),
    computed(() => store.loadingTopics)
  )
  const redirecting = ref(false)
  // 清单还在路上，「找不到」就还不是事实；这个 id 正被直接问着的时候（深链接走的那条
  // 路）也一样。
  const resolving = computed(
    () => !selectedTopic.value && (store.loadingTopics || store.topics.length === 0 || placeResolving.value)
  )

  /** 正在看的这一段对话：任务页是任务的，否则是频道自己的。已读游标记在它上面。 */
  const conversationId = computed(() => opts.place().taskId ?? opts.place().topicId)

  // 「新消息从哪开始」只有开话题的那一瞬间知道：markRead 一跑，未读数就归零了。所以在
  // 归零之前抓一次，交给对话栏去画那条线。
  const unreadOnOpen = store.unreadMap[opts.place().topicId]?.messages ?? 0

  let opened = false
  /** 先等它答完再记已读：已读位只有房间有，不知道这是房间还是支线就记，等于对每一条
   *  支线都白打一次会 404 的请求。 */
  async function openPlace() {
    const { projectId, topicId } = opts.place()
    if (!selectedTopic.value) await readProjectTopic(projectId, topicId).catch(() => null)
    if (selectedTopic.value) {
      opened = true
      store.markRead(conversationId.value)
      return
    }
    if (opts.place().taskId) return
    redirecting.value = true
    try {
      const task = await getTask(topicId)
      if (task.project_id !== projectId) return
      await router.replace({
        name: 'workspace-task',
        params: { projectId, topicId: task.room_id, taskId: task.id },
        query: route.query,
      })
    } catch {
      // Not a task either: the empty state below says so.
    } finally {
      redirecting.value = false
    }
  }

  /** 第一次由 openPlace 记已读；之后在同一个频道里进出任务，页面不重建，换到哪段对话
   *  就是读了哪段。 */
  function markReadOnSwitch() {
    if (opened) store.markRead(conversationId.value)
  }

  const place: ComputedRef<Topic | null> = selectedTopic
  return {
    place,
    resolving,
    redirecting,
    conversationId,
    unreadOnOpen,
    panelTab,
    onPanelTab,
    panelRoutineId,
    phone,
    focusBlock,
    threadFocusBlock,
    backToRoom,
    openTopic,
    jumpTo,
    onReview,
    openPlace,
    markReadOnSwitch,
  }
}
