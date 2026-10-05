// 「现场」那一格的取数：这一窗 transcript（读最近一页、往上翻、socket 上来的一行
// 落到哪儿）、按队友筛的那排名册、每一轮的起始时间、贴在顶上那条会话栏，以及摊开
// 一步之后它打印了什么。
//
// 和画的那一半（`components/panels/PanelSite.vue`）分家，理由和别处一样：场景棘轮
// 认的「场景」是 `components/panels/**` 下每个 SFC，A 档的意思是「给一组 props 就
// 能单独出画面」，取数一滴都不能漏进去。而这一格原先自己引着 `useSiteTranscript`
// （贴着接口层）和 `SessionInspector`（自己引三个接口函数），三条链一起把它拖成 C。
//
// 外壳（`components/work/PanelSiteHost.vue`）调这一次，整包递给展示组件。
import type { AgentControlState, Block } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { getStepOutput } from '../api'
import { isNarration } from '../lib/siteLog'

import { useSessionInspector } from './useSessionInspector'
import { useSiteClamp } from './useSiteClamp'
import { useSiteTranscript } from './useSiteTranscript'
import { useStickToBottom } from './useStickToBottom'

export interface PanelSiteScope {
  /** 这段对话的 id：房间的，或者任务的。 */
  topicId: () => string | null
  /** 这一格在不在屏幕上。打开的那一刻才读，往后一直往上接。 */
  active: () => boolean
  /** 一轮结束时加一：趁这时把这一段安静地重读一遍，补上 socket 断开时漏掉的行。 */
  refreshTick: () => number
  /** 在跑的轮次 id → 开始时间（毫秒），对话栏从 socket 上算的。 */
  runningTurns: () => Record<string, number> | undefined
  /** 房间 socket 上最近一帧会话状态（对话栏收到，经 TopicView 转过来）。 */
  agentControl: () => AgentControlState | null | undefined
}

export function usePanelSite(scope: PanelSiteScope) {
  // 每一轮从什么时候开始（毫秒），读到的每一页都带着它那几轮的。组头的用时从这里算起。
  const turnStarts = ref<Record<string, number>>({})

  function noteStarts(starts: Record<string, string> | undefined): void {
    const parsed = Object.entries(starts ?? {}).map(([id, at]) => [id, Date.parse(at)] as const)
    const fresh = parsed.filter(([id, at]) => Number.isFinite(at) && turnStarts.value[id] !== at)
    if (fresh.length) turnStarts.value = { ...turnStarts.value, ...Object.fromEntries(fresh) }
  }

  // The scroll container, so the timeline can open on its newest entry the way a
  // chat log does. Measured before this existed: opening 现场 left scrollTop at 0
  // with a scrollHeight of 1818 and a viewport of 500 — the reader landed 1300px
  // above the thing they came to see.
  const scrollRef = ref<HTMLElement | null>(null)
  useStickToBottom(scrollRef, 48)
  // Which entries are long enough to clamp. The measurement — together with the
  // ResizeObserver that re-reads it when the panel's own width changes — lives in
  // useSiteClamp; 每次渲染之后再读一次的时机在展示组件那一边（它的 onUpdated）。
  const { overflowing, measured, schedule: measureClamp } = useSiteClamp(scrollRef)

  // ---- 按队友看 ----
  // 一个房间可以先后、甚至同时交给几个队友。时间线是他们交错着的，而人来看的往往
  // 是其中一个在干什么。作者就是做这一步的那个队友：做过一步、说过一句的参与者。
  // 平台自己的话（署名 system）和人的动作只在「全部」里。
  //
  // 这一排 tab 是**攒出来**的，不是每次从手上那一窗里现算的：只看一个队友时手上只有
  // 那个人的行，现算的话这一排会当场塌成一个 tab，人就切不回去了。每读一页都往里
  // 记新露面的（从新到旧地翻，一个都不会漏），socket 上来的一行也记。
  const agents = ref<string[]>([])

  function noteAgents(blocks: Block[]): void {
    const seen = new Set(agents.value)
    let grew = false
    const next = agents.value.slice()
    for (const b of blocks) {
      if (b.author_type !== 'participant' || seen.has(b.author)) continue
      if (!b.meta?.tool && !isNarration(b.meta)) continue
      seen.add(b.author)
      next.push(b.author)
      grew = true
    }
    if (grew) agents.value = next
  }

  // null = 全部。
  const selectedAgent = ref<string | null>(null)
  const viewing = computed(() =>
    selectedAgent.value !== null && agents.value.includes(selectedAgent.value) ? selectedAgent.value : null
  )

  // 手上这一窗 transcript（读最近一页 / 往上翻 / 过 MAX_WINDOW 封顶 / socket 上来的行
  // 落到哪儿）都在 useSiteTranscript 里。递进去的是「现在读的是谁」和这一窗自己的滚动
  // 容器；读回来的人由 noteAgents / noteStarts 记到这一栏自己的名册和轮次上。
  const { transcript, hasOlder, loading, loadingOlder, errorMsg, load, onSiteScroll, receive } = useSiteTranscript({
    topicId: scope.topicId,
    viewing: () => viewing.value,
    scrollRef,
    noteAgents,
    noteStarts,
  })

  // 换一个视角 = 换一条时间线：重读这个人的最近一页，停在最新的那一条。不是把手上
  // 这一窗（可能是「全部」，也可能是上一位）就地滤一遍 —— 那既是「一次加载全部」，
  // 也停在原地，而人是来看这个人刚刚在干什么的。
  function selectAgent(next: string | null): void {
    if (next === selectedAgent.value) return
    selectedAgent.value = next
    void load()
  }

  // Opening the tab loads it, exactly like opening the drawer used to. 它读的是
  // 「现在看的是谁」，所以要等在 `viewing` 之后 —— immediate 的那一次是当场跑的。
  watch(
    () => scope.active(),
    (on) => {
      if (on) void load()
    },
    { immediate: true }
  )

  // 一轮刚结束：开着的这一栏安静地重读一遍。
  watch(
    () => scope.refreshTick(),
    () => {
      if (scope.active()) void load()
    }
  )

  // 在跑的那一轮，这一页读回来时可能还没登记：对话栏从 socket 上知道它从什么时候开始。
  // 记下来，这一轮停了、重读还没回来的那一会儿，用时也不缩回去。
  watch(
    () => scope.runningTurns(),
    (running) => {
      const unseen = Object.entries(running ?? {}).filter(([id]) => !(id in turnStarts.value))
      if (unseen.length) turnStarts.value = { ...Object.fromEntries(unseen), ...turnStarts.value }
    },
    { immediate: true }
  )

  // 顶上的会话栏：它自己那一套（轮询、手上有哪几个会话、问一句只读的话）在
  // `useSessionInspector` 里，这一格只管把这一包递下去。
  const inspector = useSessionInspector({
    topicId: scope.topicId,
    active: scope.active,
    pushed: scope.agentControl,
  })

  /** 摊开的那一步打印了什么。第一次点开才取（见 `SiteStepOutput.vue`）。 */
  async function loadStepOutput(blockId: string): Promise<string> {
    const topicId = scope.topicId()
    if (!topicId) return ''
    return (await getStepOutput(topicId, blockId)).output
  }

  return {
    turnStarts,
    scrollRef,
    overflowing,
    measured,
    measureClamp,
    agents,
    viewing,
    transcript,
    hasOlder,
    loading,
    loadingOlder,
    errorMsg,
    onSiteScroll,
    receive,
    selectAgent,
    inspector,
    loadStepOutput,
  }
}

/** 现场那一格的取数（`components/work/PanelSiteHost.vue` 调一次）。 */
export type PanelSiteBundle = ReturnType<typeof usePanelSite>
