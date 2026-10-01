// 项目侧栏（`TopicSidebar`）那一半「状态」：拍平的树、两组话题、谁收着、收起来的
// 那些行把未读和状态交给谁、以及那只让红灯自己亮起来的慢钟。
//
// 和画的那一半分家的理由，和 #2158 拆 PanelPreview 是同一条：这些东西原先长在
// 那个 1887 行的组件里，于是「折叠记不记得住」「红灯会不会自己亮」只能连着整条
// 侧栏一起测。搬到这里之后，它们各自是一条能单独说的线，展示组件只认 props。
//
// **这一半不认识路由**：跳转、「我在哪」、行的 ⋯ 里那几项（要 router 才算得出链接）
// 在 `useTopicRailRoutes.ts`。分开是为了让这一半能在一个没有路由的宿主里跑起来。
import type { Ref } from 'vue'
import type { Topic } from '../cx_types'
import type { FlatRow, VisibleRow } from '../lib/topicTree'

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { replyStalled } from '../lib/replyWait'
import {
  ancestorPathIds,
  inferTopicKind,
  isMyTopic,
  loadExpandedTopics,
  loadOthersGroupOpen,
  partitionByRelevance,
  saveExpandedTopics,
  saveOthersGroupOpen,
  visibleRows,
} from '../lib/topicTree'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

/** 侧栏要画的东西全在父级的这几个 props 里；这一层不自己取数。 */
export interface TopicRailSource {
  topics: Topic[]
  selectedProjectId: string | null
  selectedTopicId: string | null
  /** 话题级未读：{topicId: count}，缺键 = 没有未读。 */
  unreadMap?: Record<string, number>
  /** 私聊未读：{peerHandle: count}。侧栏只用它的总数。 */
  privateUnreadMap?: Record<string, number>
}

/** 拍平树的一行：话题 + 缩进深度。 */
export interface TreeRow {
  topic: Topic
  depth: number
}

/** 一组行：一组一个组头，两组的行是同一种形态。 */
export interface RailSection {
  key: string
  label: string
  head: boolean
  count: number
  unread: number
  open: boolean
  rows: VisibleRow<Topic>[]
}

export function useTopicRail(source: TopicRailSource) {
  const store = useWorkspaceStore()

  // ---- 树 ----
  // 边栏画的是房间。房间里派出去的活是**卡**，不是地点，看得见的地方是那个房间的
  // 看板（总览那一格）和项目级那块板 —— 一行一个房间，一件活不再占一行。
  const tree = computed<TreeRow[]>(() => {
    const all = source.topics
    const childrenOf = new Map<string | null, Topic[]>()
    for (const t of all) {
      const key = t.parent_id ?? null
      const arr = childrenOf.get(key) ?? []
      arr.push(t)
      childrenOf.set(key, arr)
    }

    const rows: TreeRow[] = []
    const seen = new Set<string>()

    const visit = (t: Topic, depth: number) => {
      if (seen.has(t.id)) return
      seen.add(t.id)
      rows.push({ topic: t, depth })
      for (const child of childrenOf.get(t.id) ?? []) visit(child, depth + 1)
    }

    // The root topic (本体) has its own pinned 全局 row above the list — show its
    // children (work topics) at depth 0, then any other top-level topics.
    const idSet = new Set(all.map((t) => t.id))
    const roots = all.filter((t) => !t.parent_id || !idSet.has(t.parent_id))
    const rootTopicNode = roots.find((t) => inferTopicKind(t) === 'root')
    if (rootTopicNode) {
      seen.add(rootTopicNode.id)
      for (const child of childrenOf.get(rootTopicNode.id) ?? []) visit(child, 0)
    }
    for (const r of roots) {
      if (inferTopicKind(r) !== 'root') visit(r, 0)
    }

    // Safety: append any orphans not reached (cycles / dangling parents).
    for (const t of all) if (!seen.has(t.id)) rows.push({ topic: t, depth: 0 })

    return rows
  })

  // 归档去向: archived topics leave the active tree and live in a collapsed
  // 「已归档」 group at the bottom (newest archived first). Non-archived children
  // of an archived parent stay in the active list (their work isn't done).
  //
  // 但**活跟着它的房间走**：活只有 open/closed，没有「已归档」这个状态，所以房间
  // 子话题有自己的归档状态：父话题归了、它还活着，那份活儿没做完，照旧留在活跃
  // 列表里——只是父行没了，深度提到 0，免得被画到隔壁那棵树底下。
  const activeTree = computed<TreeRow[]>(() => {
    // 深度按**留下来的那个父行**重新算，不沿用原树的：拍平的树里深度就是父子关系
    // 本身，中间少一层就得少一层缩进，否则缩进指着一行不存在的父行。
    const depths = new Map<string, number>()
    const rows: TreeRow[] = []
    for (const row of tree.value) {
      if (row.topic.status === 'archived') continue
      const parentId = row.topic.parent_id
      const parentDepth = parentId ? depths.get(parentId) : undefined
      const depth = parentDepth === undefined ? 0 : parentDepth + 1
      depths.set(row.topic.id, depth)
      rows.push(depth === row.depth ? row : { topic: row.topic, depth })
    }
    return rows
  })

  const archivedRows = computed<Topic[]>(() =>
    source.topics
      .filter((t) => t.status === 'archived' && inferTopicKind(t) !== 'root')
      .sort((a, b) => (b.archived_at ?? '').localeCompare(a.archived_at ?? ''))
  )

  // ---- 未读 ----
  function unreadOf(id: string): number {
    return source.unreadMap?.[id] ?? 0
  }
  // 私聊未读的总数——侧栏只说「有几条」，不说是谁。
  const privateUnreadTotal = computed<number>(() =>
    Object.values(source.privateUnreadMap ?? {}).reduce((sum, n) => sum + n, 0)
  )
  // Unread hiding inside the collapsed archived group still deserves a hint.
  const archivedUnread = computed<number>(() => archivedRows.value.reduce((sum, t) => sum + unreadOf(t.id), 0))

  // ---- 状态查表 ----
  // 折叠聚合要按 id 问「这个话题在跑吗 / 在等人吗」，而拍平树里只留了 id。走一遍
  // props.topics 建索引，别在每一行上做线性查找。
  const topicById = computed(() => new Map(source.topics.map((t) => [t.id, t])))
  function mergingOf(id: string): boolean {
    return topicById.value.get(id)?.merging === true
  }
  function runningOf(id: string): boolean {
    return topicById.value.get(id)?.running === true
  }
  function awaitsOf(id: string): boolean {
    return topicById.value.get(id)?.awaits_me === true
  }
  function failedOf(id: string): boolean {
    return Boolean(topicById.value.get(id)?.turn_failed_at)
  }

  // 红灯要跟着钟亮：列表三十分钟才刷一次，而「等满五分钟」是时间自己走到的，不是
  // 数据变出来的。所以这里自己有一只慢钟，每 10 秒拨一下让判断重算。
  const clock = ref(Date.now())
  let clockTimer: number | undefined
  onMounted(() => {
    clockTimer = window.setInterval(() => (clock.value = Date.now()), 10_000)
  })
  onUnmounted(() => {
    if (clockTimer !== undefined) window.clearInterval(clockTimer)
  })

  // 红灯两个来源：最近一轮报错了（立刻亮），或有人 @ 了 AI 等满五分钟没回话。
  function stalledOf(id: string): boolean {
    const topic = topicById.value.get(id)
    return failedOf(id) || replyStalled(topic?.awaiting_reply_since, clock.value, topic?.reply_wait_reason)
  }

  // ---- 折叠 ----
  // 一个房间下面挂的是**它派出去的活**，不是子话题。范式跟底部的「已归档」分组
  // 一致（一个 chevron 收起一堆行），只是这里的开关长在每一个有子话题的行上。行的
  // 可见性/未读聚合是纯逻辑，住在 lib/topicTree.ts 里（有单测），这里只管状态和落盘。
  //
  // 默认收起，展开是个动作。按项目存 localStorage（而不是只放内存）：这个 rail 是
  // 主导航，每次刷新都要重展一遍等于没有记住。存的是**展开的** id，所以新派出去的
  // 活天然是收起来的。
  const expandedIds = ref<ReadonlySet<string>>(new Set<string>())
  // `visibleRows` 问的是「哪些行是收起来的」，而我们记的是展开过的那些 —— 有孩子
  // 的行里，没被展开过的就是收起来的。
  const collapsedIds = computed<ReadonlySet<string>>(() => {
    const withChildren = new Set<string>()
    for (const t of source.topics) {
      if (t.parent_id && !expandedIds.value.has(t.parent_id)) withChildren.add(t.parent_id)
    }
    return withChildren
  })

  // 「其他话题」这一组展开没展开。默认折叠——这一整条改动的意义就在这里，所以它
  // 也按项目落盘（键不在 = 折叠，见 lib/topicTree.ts）。
  const othersOpen = ref(false)
  watch(
    () => source.selectedProjectId,
    (pid) => {
      expandedIds.value = loadExpandedTopics(pid)
      othersOpen.value = loadOthersGroupOpen(pid)
    },
    { immediate: true }
  )

  // 当前选中话题的祖先链：这条路径无论祖先收没收起来都照常渲染，所以"人正待在
  // 里面的那个话题"永远不会被折叠藏掉。用 reveal 而不是"自动展开"，是为了不把
  // 用户自己设的折叠状态在导航时偷偷改写——离开之后那一支照旧是收起来的。
  watch(
    () => source.selectedTopicId,
    async (id) => {
      if (!id) return
      await nextTick()
      document.querySelector(`[data-room-id="${CSS.escape(id)}"]`)?.scrollIntoView?.({ block: 'nearest' })
    }
  )
  const selectedPath = computed(() => ancestorPathIds(source.topics, source.selectedTopicId))

  function toggleCollapse(id: string) {
    const next = new Set(expandedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    expandedIds.value = next
    saveExpandedTopics(source.selectedProjectId, next)
  }

  // ---- 分组 (C2): 我参与的平铺，其他话题收进一个默认折叠的组 ----
  // 判定住在 lib/topicTree.ts 里（纯函数 + 单测），这里只管接线、组的开关和落盘。
  //
  // 两组的**行是同一种形态**：同一段模板渲染，所以树形缩进、竖向引导线、16px 状态
  // 槽、未读角标、hover 的 ⋯ 一个不少。折叠组只是把一批行收起来，不是换一种行。
  const grouped = computed(() => partitionByRelevance(activeTree.value, isMyTopic))

  function rowsOf(rows: readonly FlatRow<Topic>[]) {
    return visibleRows(rows, {
      collapsed: collapsedIds.value,
      reveal: selectedPath.value,
      unreadOf,
      runningOf,
      mergingOf,
      awaitsOf,
      stalledOf,
    })
  }

  const mineTree = computed(() => rowsOf(grouped.value.mine))
  const othersTree = computed(() => rowsOf(grouped.value.others))
  const othersCount = computed(() => grouped.value.others.length)

  // 组头的未读聚合成**一个点**，不是数字：别人话题里有几条新消息与我无关，但"那边
  // 有动静"值得知道。算的是整组（含组内自己收起来的子话题），所以点在不在，不受
  // 组内折叠状态影响。
  const othersUnread = computed<number>(() => grouped.value.others.reduce((sum, r) => sum + unreadOf(r.topic.id), 0))

  // 选中的话题落在这一组里时，通往它的那条路径照常渲染——和折叠一个父话题时的
  // reveal 一模一样。落盘的偏好一个字都不动，离开之后这一组照旧是收着的。
  const othersHoldsSelected = computed(() =>
    source.selectedTopicId ? grouped.value.others.some((r) => r.topic.id === source.selectedTopicId) : false
  )
  const othersRendered = computed(() => {
    if (othersOpen.value) return othersTree.value
    if (!othersHoldsSelected.value) return []
    return othersTree.value.filter((r) => selectedPath.value.has(r.topic.id))
  })

  function toggleOthers() {
    othersOpen.value = !othersOpen.value
    saveOthersGroupOpen(source.selectedProjectId, othersOpen.value)
  }

  // 一次 v-for 走完两组，所以话题行的那段模板只存在一份——「形态一致」是结构保证
  // 的，不是靠两处复制的模板保持同步。组头只有下面那一组有。
  const railSections = computed<RailSection[]>(() => [
    { key: 'mine', label: '', head: false, count: 0, unread: 0, open: true, rows: mineTree.value },
    {
      key: 'others',
      label: t('work.sidebar.others'),
      head: othersCount.value > 0,
      count: othersCount.value,
      unread: othersUnread.value,
      // open 只管组头那个 chevron 的朝向 = 用户设的值；实际渲染哪些行看 rows。
      open: othersOpen.value,
      rows: othersRendered.value,
    },
  ])

  // The root topic (本体) — the pinned 「全局」 row at the top of the list.
  const rootTopic = computed<Topic | null>(() => source.topics.find((t) => inferTopicKind(t) === 'root') ?? null)

  /** 侧栏提示里说的那个队友的名字，不写死「芝士」。 */
  const agentName = computed(() => store.agentName)

  // 行的折叠开关 hover 那一句：收着的时候要说清里面有什么（展开之后就不必了）。
  function toggleTitle(row: VisibleRow<Topic>): string {
    if (!row.collapsed) return t('work.sidebar.collapse')
    if (row.hiddenStalled) return t('work.sidebar.expandStalled')
    if (row.hiddenAwaits) return t('work.sidebar.expandAwaits')
    if (row.hiddenRunning) return t('work.sidebar.expandRunning', { agent: agentName.value })
    if (row.hiddenMerging) return t('work.sidebar.expandMerging')
    return t('work.sidebar.expand')
  }

  return {
    // 树与分组
    rootTopic,
    activeTree,
    archivedRows,
    mineTree,
    othersTree,
    othersRendered,
    othersOpen,
    othersCount,
    othersUnread,
    othersHoldsSelected,
    toggleOthers,
    railSections,
    // 折叠
    toggleCollapse,
    // 状态
    topicById,
    unreadOf,
    privateUnreadTotal,
    archivedUnread,
    stalledOf,
    awaitsOf,
    runningOf,
    mergingOf,
    toggleTitle,
    agentName,
    /** 这只钟每 10 秒拨一下；行把它收成 prop，自己算「等了多久」。 */
    clock: clock as Ref<number>,
  }
}
