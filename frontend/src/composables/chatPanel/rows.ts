// 画得出来的那几行：哪些块露面、日期线画在哪、一段话从哪一行续到哪一行、新消息线钉在
// 哪条上，以及跟着这些块一起来的任务。
//
// 判据本身是纯函数（lib/chatGrouping.ts 和 lib/topicState.ts，测试就在它们旁边）；这里
// 只把判据接到此刻的这几行上，并把 `<#id>` 的标题表填好——chips 画的是它。
import type { Ref } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { groupAskBlocks } from '../../lib/askGroups'
import { dayLabelsFor, outboxEdgeAfter, type RunEdge, runEdgeBetween, unreadAnchorBlock } from '../../lib/chatGrouping'
import { collapseNotices } from '../../lib/platformNotice'
import { coalesceSplitFencedCodeBlocks } from '../../lib/renderMessage'
import { taskTitle, topicTitle } from '../../lib/topicState'
import { useTimelineTasks } from '../useTimelineTasks'

/** 这一栏画出来的行，和它们的分组。 */
export function usePanelRows(deps: {
  /** 时间线此刻的块（useTimeline 的 messages）。 */
  messages: Ref<Block[]>
  /** 打开这一栏时这间房有几条未读。 */
  unreadOnOpen: () => number
  /** 我自己的 handle。 */
  author: string
  /** 这条是不是我说的（usePanelAuthorship）。 */
  isMine: (block: Block) => boolean
  /** 这一栏读的那间房：它自己的那一行、和挂在它下面的任务。 */
  topic: () => Topic | null
  /** 这个项目里叫得出名字的话题，用来解 `<#id>` 是话题还是任务。 */
  topicList: () => Topic[]
  /** 换掉时间线上的一块（任务重读后带着新的 `tasks` 换回去）。 */
  replace: (block: Block) => void
  /** chips 的标题表（refMaps.topicTitles）。 */
  titles: Record<string, string>
}) {
  // The conversation stream shows messages + lightweight system lines only.
  // doc blocks are document state (they live in the doc panel), and AI tool
  // events belong in 现场 — neither belongs in the group chat (spec §7.1).
  // Historical SDK turns can contain one fenced Markdown block split across
  // consecutive message rows. Repair those rows before collapseNotices hides
  // event blocks, because an event is a hard boundary and must prevent an
  // accidental merge.
  const collapsed = computed(() => collapseNotices(coalesceSplitFencedCodeBlocks(deps.messages.value)))
  // 一次问的几道题收成一张卡（`lib/askGroups`）：头一条留着画整组，其余几条不再
  // 单独占一行——它们的内容已经在头一条那张卡里了。判据是纯函数，测试在它旁边。
  const askBlocks = computed(() => groupAskBlocks(collapsed.value.map((r) => r.block)))
  const rows = computed(() => {
    if (!askBlocks.value.hidden.size) return collapsed.value
    return collapsed.value.filter((r) => !askBlocks.value.hidden.has(r.block.id))
  })
  const visible = computed<Block[]>(() => rows.value.map((r) => r.block))

  // 对话栏画得出来的那几件任务跟着各块一起来（useTimelineTasks），不读整个房间的清单。
  const roomTasks = useTimelineTasks({
    roomId: () => deps.topic()?.id,
    blocks: visible,
    topicIds: () => new Set(deps.topicList().map((t) => t.id)),
    replace: deps.replace,
  })

  // ---- 时间刻度 ----
  // 哪一条属于哪一天、日期线画在它上面：规则和判据在 lib/chatGrouping.ts（纯函数，
  // 那份测试就在旁边），这里只是把它接到此刻的这几行上。
  const dayLabels = computed(() => dayLabelsFor(rows.value))

  // 新消息线锚在哪条块上。开话题时按当时的未读数往回数一次就冻住 —— 它是「我上次
  // 看到哪儿」的记号，不是一个会跟着新消息跑的游标。
  const unreadAnchorId = ref<string | null>(null)
  function placeUnreadAnchor() {
    unreadAnchorId.value = unreadAnchorBlock(rows.value, deps.unreadOnOpen(), deps.author)
  }

  // <#id> 可以指一个话题，也可以指这个房间里的一件活：两边的标题都得认得，否则活的
  // chip 只会写「#话题」。
  // 只改变了的那几个：每条消息正文都盯着这张表，整张重填会让整条时间线重画（见 useChatScroll）。
  watch(
    [deps.topicList, roomTasks.known],
    ([ts, tasks]) => {
      const next = new Map<string, string>()
      for (const t of ts) next.set(t.id, topicTitle(t))
      for (const t of tasks.values()) next.set(t.id, taskTitle(t))
      for (const k of Object.keys(deps.titles)) if (!next.has(k)) delete deps.titles[k]
      for (const [k, title] of next) if (deps.titles[k] !== title) deps.titles[k] = title
    },
    { immediate: true, deep: true }
  )

  // 一段话从哪一行开始、哪一行是同一段的续话、发件箱那几条接在谁后面：规则在
  // lib/chatGrouping.ts，这里只回答「上面有没有插进别的行」——新消息线会把一段话切断。
  const runEdges = computed(() =>
    visible.value.map((cur, i) =>
      runEdgeBetween(visible.value[i - 1], cur, {
        broken: cur.id === unreadAnchorId.value,
      })
    )
  )

  function outboxEdge(index: number): RunEdge {
    if (index > 0) return 'cont'
    const last = visible.value.at(-1)
    return outboxEdgeAfter(last, {
      mine: !!last && deps.isMine(last),
    })
  }

  /** 组头块的 id → 这一组的每道题；组件拿它画整张卡。 */
  const askGroups = computed(() => askBlocks.value.members)

  return {
    rows,
    visible,
    askGroups,
    roomTasks,
    dayLabels,
    unreadAnchorId,
    placeUnreadAnchor,
    runEdges,
    outboxEdge,
  }
}
