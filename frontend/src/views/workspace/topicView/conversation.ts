// 这一段对话：哪一条支线开着、任务页那一份任务、以及这个页面上每一处署名该叫什么。
//
// 支线：主线上一条消息的支线有就打开，没有就先开一条；概览里「支线」那一格读同一份
// 清单。也可以在频道下手动开一条（清单第 4 条）：填个标题就开，不必先有话，开了之后
// 直接落进那条支线——建出来就是为了马上在里面说话，停在原地等于让人再点一次。
//
// 任务页：页头、概览、能不能说话都读这一份；对话和面板的其余几格按任务的 id 自己读。
// 任务交给谁、请谁协作，从项目里的人挑：被选中的人随之加入这个频道（后端）。
//
// 署名：这个房间名册上每个 handle 叫什么。「现场」那一格给每一行署名用它，人和 AI 队
// 友一个规矩：署作者，不署「这个房间的那位」——一个房间可以先后交给两个队友。那一格
// 自己不拉名册，所以在这里拉一次传下去。AI 队友的名字和对话栏同一个出处
// （`agentNames`）：已经不在这间房里的队友，项目名册上还叫得出。名册拉不到，现场那一
// 格就按 handle 署名——比空白好，也比报错好。名册抽屉里加了人、移了人，这份当场重读：
// 刚请进来的队友在「现场」那一格也要叫得出名字。
import type { Topic, TopicMemberRow } from '@/cx_types'
import type { CardPhase } from '@/lib/topicState'
import type { ThreadRow } from '@/types/threads'
import type { TopicPlace } from './shared'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { useChannelSpeakers } from '@/composables/useChannelSpeakers'
import { useChannelThreads } from '@/composables/useChannelThreads'

import { newThread, restoreThread } from '@/api/threads'
import { t } from '@/i18n'
import { agentNames, memberName } from '@/lib/agentNames'
import { dayStamp } from '@/lib/dateText'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { roomMembersQuery } from '@/query/room'
import { useWorkspaceStore } from '@/stores/workspace'
import { useConversationOpening } from '@/views/workspace/useConversationOpening'
import { useTaskPage } from '@/views/workspace/useTaskPage'

export function useTopicConversation(opts: {
  place: () => TopicPlace
  room: () => Topic | null
  /** 第一次由打开地址那一下记已读；之后在同一个频道里进出任务，换到哪段对话就是读了
   *  哪段。 */
  onPlaceSwitch: () => void
}) {
  const store = useWorkspaceStore()
  const router = useRouter()

  // ---- 支线 ----
  const channelThreads = useChannelThreads(() => (opts.place().taskId ? null : opts.place().topicId))
  const { showThread, onOpenThread, openTaskPage, onOpenCard, upgradeMessage } = useConversationOpening({
    router,
    place: opts.place,
    markSeen: channelThreads.markSeen,
  })

  const newThreadOpen = ref(false)
  const creatingThread = ref(false)
  const canOpenThread = computed(() => {
    const topic = opts.room()
    return !!topic && topic.status !== 'archived' && (topic.kind === 'root' || !!topic.joined)
  })
  async function createThread(title: string) {
    creatingThread.value = true
    try {
      const thread = await newThread(opts.place().topicId, title)
      newThreadOpen.value = false
      channelThreads.load()
      showThread(thread.id)
    } catch (e) {
      store.reportError(e, t('work.room.thread.createFailed'))
    } finally {
      creatingThread.value = false
    }
  }
  // 把一条收起来的支线放回来（`thread/recycle.py`）。只有开这条支线的人和频道管理员
  // 点得动，后端是最后一道（`threadRestoreNotYours`）；这里只是把按钮少画给不能的人。
  const restoringThread = ref(false)
  function canRestoreThread(row: ThreadRow): boolean {
    if (!myHandle()) return false
    if (opts.room()?.can_manage) return true
    // 开这条支线的人（`threads.created_by`），不是「说话的第一个人」：
    // `participants` 的头一个是挂着的那条消息的作者，两者常常不是同一个人。
    return row.created_by === myHandle()
  }
  async function restoreAThread(threadId: string) {
    restoringThread.value = true
    try {
      await restoreThread(threadId)
      channelThreads.markRestored(threadId)
    } catch (e) {
      store.reportError(e, t('work.room.thread.restoreFailed'))
    } finally {
      restoringThread.value = false
    }
  }
  /** 支线清单里最后一条回复的时间：今天的写钟点，更早的写日期。 */
  function threadTime(iso: string): string {
    return dayStamp(new Date(iso).getTime())
  }

  // ---- 任务页 ----
  const AUTHOR = myHandle()
  const projectPeople = computed<TopicMemberRow[]>(() =>
    store.members
      .filter((m) => !m.agent && m.active !== false)
      .map((m) => ({
        topic_id: opts.place().topicId,
        member_handle: m.user_handle,
        role: 'member' as const,
        name: m.name,
        remark: m.remark ?? null,
        avatar_id: m.avatar_id ?? null,
      }))
  )
  const taskPage = useTaskPage({ taskId: () => opts.place().taskId, people: () => projectPeople.value })
  watch(
    () => opts.place().taskId,
    () => {
      taskPage.reset()
      void taskPage.load()
      opts.onPlaceSwitch()
    },
    { immediate: true }
  )
  const {
    task: currentTask,
    loading: taskLoading,
    loadError: taskLoadError,
    isOwner: taskOwner,
    isOpen: taskOpen,
  } = taskPage

  const speakers = useChannelSpeakers(opts.room)

  // 任务里项目的人都能说话，关了也能接着说：AI 队友只回答，不再改动项目。频道主线只让部分
  // 角色发言、支线也不让人人回时，任务里同样只读。
  const composerClosed = computed(() => {
    if (opts.place().taskId) return speakers.notice(true)
    // 频道的主线：归档了只能看；没加入的人在这里只读，加入后才能说话（支线里照样能回）。
    const topic = opts.room()
    if (topic?.status === 'archived') return t('work.channel.archivedNotice')
    if (topic?.joined === false) return t('work.channel.notJoined')
    return speakers.notice(false)
  })

  // 提问只在这件事还开着时等回答：任务关了、频道归档了，题跟着结束（待办里同样不再列
  // 它）。
  const askClosed = computed(() =>
    opts.place().taskId ? !!taskPage.task.value && !taskPage.isOpen.value : opts.room()?.status === 'archived'
  )

  // ---- 署名 ----
  const rosterRead = useQuery(
    computed(() => roomMembersQuery(opts.place().topicId)),
    queryClient
  )
  const roomMembers = computed<TopicMemberRow[]>(() => rosterRead.data.value?.data ?? [])
  // 任务那一栏「做这件事的队友」能挑的几位：这间房名册上的 AI 队友。换的时候后端也只认
  // 名册上那个座位，所以给的就是名册。
  const roomAgents = computed<TopicMemberRow[]>(() => roomMembers.value.filter((m) => m.agent))
  const memberNames = computed<Record<string, string>>(() => ({
    ...Object.fromEntries(roomMembers.value.map((m) => [m.member_handle, memberName(m) || m.member_handle])),
    ...Object.fromEntries(agentNames(roomMembers.value, store.members)),
  }))

  return {
    channelThreads,
    showThread,
    onOpenThread,
    openTaskPage,
    onOpenCard,
    upgradeMessage,
    newThreadOpen,
    creatingThread,
    canOpenThread,
    createThread,
    restoringThread,
    canRestoreThread,
    restoreAThread,
    threadTime,
    AUTHOR,
    projectPeople,
    taskPage,
    currentTask,
    taskLoading,
    taskLoadError,
    taskOwner,
    taskOpen,
    composerClosed,
    askClosed,
    roomMembers,
    roomAgents,
    memberNames,
  }
}

/** 采纳卡处在哪一段：卡片自己管数据，但面板要按它那一段挑页签，所以这一个词往上走，
 *  而不是把卡片清单往下传。卡片还没答过时是 undefined —— 没有答案的时候面板不挑页签。
 *  房间页头不画它：卡的阶段是卡的，画在卡上和看板上。 */
export function useCardPhase(accept: { loaded: { value: boolean }; phase: { value: CardPhase | undefined } }) {
  const cardPhase = ref<CardPhase | undefined>(undefined)
  watch(
    [accept.loaded, accept.phase],
    () => {
      if (accept.loaded.value) cardPhase.value = accept.phase.value
    },
    { immediate: true }
  )
  return cardPhase
}
