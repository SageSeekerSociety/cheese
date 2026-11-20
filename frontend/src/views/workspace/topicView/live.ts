// 这个房间此刻是不是活的：一轮有没有在跑、谁在忙、会话状态和正在跑的轮次，以及一轮
// 干完和一个平台资源变了之后要重读哪几样。
//
// 「有没有队友正在这个话题里跑一轮」是工作面板的「现场」那一格和推送提示读的那一格。
// 它由对话栏按轮次生命周期报上来：一条 @芝士 开出来的 agent 可能先想上半分钟才动手，
// 那半分钟里右边什么都没有，除非刷新一次页面——所以它在开工那一刻就翻过来，而不是等
// 到第一次动手。
//
// 页头那颗点说的是「这个房间跟不跟得上」，它和工作条必须同源：对话栏报上来的 linkDown
// 已经不算刚进房间时的握手和一下就重连上的闪断（`useRoomSocket`），可 socket 断得再久
// 一点时这一轮可能还在跑（工作条写着「正在工作 · 重试中」，因为重试就是靠它自己接着
// 干）。一轮没跑完，这个房间就是连着的——断了它没法把这一轮干完。所以两个一起看。
//
// 一个平台资源变了（改它的那个接口处理函数发了这一帧）就当场重读受影响的哪几样
// （§3.1.1）。频道这一行、任务、置顶、支线在缓存里，对话栏收到帧时已经把它们标过期了
// （`query/changes`），看着它们的侧栏、概览、任务页自己重读；这里只接缓存里没有的那
// 几样。
import type { AgentControlState } from '@/cx_types'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { ChatColumnHandle } from './shared'

import { computed, ref } from 'vue'

import { useWorkspaceStore } from '@/stores/workspace'

export function useTopicLive(opts: {
  /** 正在看的这一段对话：任务页是任务的，否则是频道自己的。 */
  conversationId: () => string
  taskId: () => string | undefined
  /** 一轮干完，任务页那一份重读。 */
  reloadTask: () => void
  /** 对话收着的这段时间里有新回复：回到并排时那个点。 */
  noteChatNews: () => void
  /** 采纳卡重读。 */
  reloadAccept: () => void
  /** 技能的提议落下、被保存或被拒：对话里那张卡和右侧那一格跟着变。 */
  reloadSkills: () => void
  /** 对话栏那个把手：连接状态和跟着帧走的那两样都问它。 */
  chat: () => ChatColumnHandle | null
}) {
  const store = useWorkspaceStore()

  // 有没有队友正在这个话题里跑一轮。
  const working = ref(false)
  // 此刻谁在这个房间里忙，对话栏从 socket 上学来：现场那一格画其中在干活的队友。
  const activity = ref<MemberActivityLine[]>([])
  // 会话状态的最近一帧，现场那格的会话详情读它。
  const agentControl = ref<AgentControlState | null>(null)
  // 正在跑的轮次各自从什么时候开始，现场读它分出哪一组还在进行。
  const siteTurns = ref<Record<string, number>>({})
  // 每一轮 AI 回合 / 每一次工具调用都加一。面板里的文档和综合那一页的项目目标看着它
  // 重读芝士维护出来的东西。
  const activityTick = ref(0)
  // 对话栏报上来的连接状态：页头那颗点读它。
  const linkDown = computed(() => !!opts.chat()?.linkDown)

  const roomConnected = computed(() => !linkDown.value || working.value)

  function handleWorking(now: boolean) {
    working.value = now
  }

  function handleTurnDone() {
    // 这一轮干完了 —— 现场那条时间线就是它留下的记录。
    working.value = false
    opts.noteChatNews()
    activityTick.value += 1
    if (opts.taskId()) opts.reloadTask()
    void store.refreshTopics()
    // 芝士's reply landed after our read cursor — the user is watching this
    // topic, so re-bump the cursor before refreshing badges (other topics that
    // got messages in the background DO light up).
    store.markRead(opts.conversationId())
    void store.refreshUnread()
  }

  const CACHED_RESOURCES: ReadonlySet<string> = new Set(['topics', 'tasks', 'pins', 'chapters', 'threads'])
  function handleStateChanged(resource: string) {
    if (resource === 'accept') opts.reloadAccept()
    // 提案卡落下、被发出去、被「不用」：卡片跟着变，不等刷新。
    else if (resource === 'feedback') opts.chat()?.reloadFeedback()
    // 技能的提议落下、被保存或被拒：那张卡跟着变。
    else if (resource === 'skills') opts.reloadSkills()
    else if (!CACHED_RESOURCES.has(resource)) activityTick.value += 1 // doc / notify → reload
  }

  return {
    working,
    activity,
    agentControl,
    siteTurns,
    activityTick,
    linkDown,
    roomConnected,
    handleWorking,
    handleTurnDone,
    handleStateChanged,
  }
}
