// 项目侧栏（`TopicSidebar`）那一半「状态」：我加入了的频道、谁收着、收起来的那些行
// 把未读和成员的动静交给谁、以及那只让红标自己亮起来的慢钟。
//
// 和画的那一半分家的理由，和 #2158 拆 PanelPreview 是同一条：这些东西原先长在
// 那个 1887 行的组件里，于是「折叠记不记得住」「红灯会不会自己亮」只能连着整条
// 侧栏一起测。搬到这里之后，它们各自是一条能单独说的线，展示组件只认 props。
//
// **这一半不认识路由**：跳转、「我在哪」、行的 ⋯ 里那几项（要 router 才算得出链接）
// 在 `useTopicRailRoutes.ts`。分开是为了让这一半能在一个没有路由的宿主里跑起来。
import type { Topic } from '../cx_types'
import type { RailMemberMark } from '../lib/memberActivity'
import type { FlatRow, VisibleRow } from '../lib/topicTree'
import type { TopicUnread } from '../types/channels'

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { agentIdentities, agentNames } from '../lib/agentNames'
import { isAgentHandle } from '../lib/authorship'
import { waitStalled, waitText } from '../lib/replyWait'
import { ancestorPathIds, inferTopicKind, loadExpandedTopics, saveExpandedTopics, visibleRows } from '../lib/topicTree'
import { VIRTUAL_LIST_THRESHOLD } from '../lib/virtualList'
import { getAvatarUrl } from '../utils/materials'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

/** 侧栏要画的东西全在父级的这几个 props 里；这一层不自己取数。 */
export interface TopicRailSource {
  topics: Topic[]
  selectedProjectId: string | null
  selectedTopicId: string | null
  /** 频道和任务在等我的东西：{id: TopicUnread}，缺键 = 没有。 */
  unreadMap?: Record<string, TopicUnread>
  /** 私聊未读：{peerHandle: count}。侧栏只用它的总数。 */
  privateUnreadMap?: Record<string, number>
}

/** 拍平树的一行：话题 + 缩进深度。 */
export interface TreeRow {
  topic: Topic
  depth: number
}

/** 侧栏从外面拿到的「把某一组滚到第几行」：一组行太多、交给虚拟化之后，行不在 DOM 里
 *  （没被窗口挂上），`scrollIntoView` 够不着，只能按序号让那一组自己滚。 */
export interface RailScrollTarget {
  scrollToIndex: (sectionKey: string, index: number) => void
}

/** 一组行。侧栏现在只有一组：我加入了的频道。 */
export interface RailSection {
  key: string
  rows: VisibleRow<Topic>[]
}

/** 一位成员此刻是谁：`handle` 用来画（头像底色认它），`identity` 用来认人。 */
interface RailMemberWho {
  handle: string
  name: string
  /** 自己挑过的头像地址；没挑过、或名册上没这个人时是 null。 */
  avatar: string | null
  agent: boolean
  identity: string
}

export function useTopicRail(source: TopicRailSource, scrollTarget?: RailScrollTarget) {
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

  // 侧栏的那棵树：没归档、我加入了的频道。房间子话题有自己的归档状态：父话题归了、
  // 它还活着，就照旧留着，只是父行没了，深度提到 0。
  const activeTree = computed<TreeRow[]>(() => {
    // 深度按**留下来的那个父行**重新算，不沿用原树的：拍平的树里深度就是父子关系
    // 本身，中间少一层就得少一层缩进，否则缩进指着一行不存在的父行。
    const depths = new Map<string, number>()
    const rows: TreeRow[] = []
    for (const row of tree.value) {
      // 侧栏只列我加入了的频道，外加我此刻正看着的那一个（从「浏览频道」点进来、还没
      // 加入的）——不然人就不知道自己在哪儿。已归档的频道不在侧栏里，在项目设置的
      // 「频道」一栏。`joined` 缺席（没经过 list_topics 的载荷）当作加入了：宁可多列，
      // 不把频道静默藏起来。
      if (row.topic.status === 'archived') continue
      if (row.topic.joined === false && row.topic.id !== source.selectedTopicId) continue
      const parentId = row.topic.parent_id
      const parentDepth = parentId ? depths.get(parentId) : undefined
      const depth = parentDepth === undefined ? 0 : parentDepth + 1
      depths.set(row.topic.id, depth)
      rows.push(depth === row.depth ? row : { topic: row.topic, depth })
    }
    return rows
  })

  // ---- 未读 ----
  /** 行上的数字：频道已经按我设的通知档位算过了。 */
  function unreadOf(id: string): number {
    return source.unreadMap?.[id]?.count ?? 0
  }
  /** 有没有新消息（名字加粗）：静音的频道不加粗。 */
  function freshOf(id: string): boolean {
    return source.unreadMap?.[id]?.new === true
  }
  // 私聊未读的总数——侧栏只说「有几条」，不说是谁。
  const privateUnreadTotal = computed<number>(() =>
    Object.values(source.privateUnreadMap ?? {}).reduce((sum, n) => sum + n, 0)
  )

  // ---- 状态查表 ----
  // 折叠聚合要按 id 问「这里有队友在干活吗 / 在等人吗」，而拍平树里只留了 id。走一遍
  // props.topics 建索引，别在每一行上做线性查找。
  //
  // 房间自己没有状态：问的都是房间里的成员。侧栏只画在干活的队友，不画打字的人——
  // 这份列表隔一阵才读一次，而打字几秒就过去了，画出来多半已经不是真的了。
  const topicById = computed(() => new Map(source.topics.map((t) => [t.id, t])))
  function workersOf(topic: Topic | undefined) {
    return (topic?.activity ?? []).filter((a) => a.kind === 'working')
  }
  function workingOf(id: string): boolean {
    return workersOf(topicById.value.get(id)).length > 0
  }
  function awaitsOf(id: string): boolean {
    return topicById.value.get(id)?.awaits_me === true
  }

  // 红标要跟着钟亮：列表三十秒才刷一次，而「等满五分钟」是时间自己走到的，不是
  // 数据变出来的。所以这里自己有一只慢钟，每 10 秒拨一下让判断重算。
  const clock = ref(Date.now())
  let clockTimer: number | undefined
  onMounted(() => {
    clockTimer = window.setInterval(() => (clock.value = Date.now()), 10_000)
  })
  onUnmounted(() => {
    if (clockTimer !== undefined) window.clearInterval(clockTimer)
  })

  // 房间在等的成员里，此刻算卡住了的那几位：它那一轮报错了（立刻算），或等满了阈值。
  function stalledWaits(topic: Topic | undefined) {
    return (topic?.waits ?? []).filter((w) => waitStalled(w, clock.value))
  }
  function stalledOf(id: string): boolean {
    return stalledWaits(topicById.value.get(id)).length > 0
  }

  // 一位成员叫什么、是不是 AI 队友：项目名册说了算（队友的座位和它自己的 handle 都认）。
  // 说不出是谁的那一笔（null）记在项目默认的队友头上，handle 也得是它的：同一行里
  // 按 handle 去重、头像按 handle 取底色，给个空 handle 就成了另一位、另一种颜色。
  const agentNameMap = computed(() => agentNames([], store.members))
  const agentIdentityMap = computed(() => agentIdentities([], store.members))
  function memberOf(handle: string | null): RailMemberWho {
    if (!handle) {
      const fallback = store.agentHandle ?? ''
      return { handle: fallback, name: agentName.value, avatar: null, agent: true, identity: fallback }
    }
    const agent = agentNameMap.value.get(handle)
    if (agent) {
      return {
        handle,
        name: agent,
        avatar: null,
        agent: true,
        identity: agentIdentityMap.value.get(handle) ?? handle,
      }
    }
    if (isAgentHandle(handle)) {
      return { handle, name: agentName.value, avatar: null, agent: true, identity: handle }
    }
    const person = store.members.find((m) => m.user_handle === handle)
    return {
      handle,
      name: person?.name || handle,
      // 项目名册上就带着这个人挑过的头像 id，侧栏这一格原来没用它，于是真人永远只有
      // 首字母。没挑过（avatar_id 为 null）就不取图，交给首字母。
      avatar: person?.avatar_id != null ? getAvatarUrl(person.avatar_id) : null,
      agent: false,
      identity: handle,
    }
  }

  /** 这一行上画的那几位成员：卡住了的在前（红），在干活的在后（绿）。同一位只画一次。 */
  function memberMarks(topic: Topic): RailMemberMark[] {
    const marks: RailMemberMark[] = []
    const drawn = new Set<string>()
    const add = (who: RailMemberWho, state: RailMemberMark['state'], title: string) => {
      if (drawn.has(who.identity)) return
      drawn.add(who.identity)
      marks.push({
        handle: who.handle,
        name: who.name,
        avatar: who.avatar,
        agent: who.agent,
        state,
        title,
      })
    }
    for (const wait of stalledWaits(topic)) {
      const who = memberOf(wait.member)
      add(who, 'stalled', waitText(wait, who.name, clock.value))
    }
    for (const work of workersOf(topic)) {
      const who = memberOf(work.member)
      add(who, 'working', t('work.sidebar.workingTip', { name: who.name }))
    }
    return marks
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

  watch(
    () => source.selectedProjectId,
    (pid) => {
      expandedIds.value = loadExpandedTopics(pid)
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
      // 选中的那一行在不在一份虚拟化的列表里？在的话它多半**不在 DOM 里**（没被窗口
      // 挂上），`scrollIntoView` 够不着它——只能按序号让那一组自己滚过去。整列都在
      // DOM 里时照旧走 querySelector：它只滚「最近的那一段」，不把整列跳一下。
      //
      // 归档组走的是下面那条兜底：它不在 railSections 里（那一组自己管收展），但它的行
      // 同样带 `data-room-id`、选中的那一行同样常驻 DOM，所以按 id 找得到、滚得动。
      const at = selectedLocation.value
      if (at && scrollTarget && isVirtualSection(at.key)) {
        scrollTarget.scrollToIndex(at.key, at.index)
        return
      }
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

  const railRows = computed(() =>
    visibleRows(activeTree.value, {
      collapsed: collapsedIds.value,
      reveal: selectedPath.value,
      unreadOf,
      workingOf,
      awaitsOf,
      stalledOf,
    })
  )

  const railSections = computed<RailSection[]>(() => [{ key: 'mine', rows: railRows.value }])

  // 选中的话题落在哪一组、那一组里排第几行。上面那条 watch 要按**序号**滚虚拟化的
  // 那一组（那种时候行不在 DOM 里，光有 id 够不着），所以除了「在不在这一组」还得知道
  // 它排第几。收起来的子树里的行不在 rows 里，也就落不到这儿（那种行本来也不在屏幕上）。
  // 归档组不在 railSections 里（它的收展是那个组件自己的状态），所以它也落不到这儿——
  // 它走 watch 里那条 `querySelector` 兜底，靠的是归档行的 `data-room-id`。
  const selectedLocation = computed<{ key: string; index: number } | null>(() => {
    const id = source.selectedTopicId
    if (!id) return null
    for (const section of railSections.value) {
      const index = section.rows.findIndex((row) => row.topic.id === id)
      if (index >= 0) return { key: section.key, index }
    }
    return null
  })

  /** 这一组行数过门槛了吗？过的人和 `VirtualList` 用同一个常量，两边不能各记一个数。 */
  function isVirtualSection(key: string): boolean {
    const section = railSections.value.find((s) => s.key === key)
    return !!section && section.rows.length > VIRTUAL_LIST_THRESHOLD
  }

  // The root topic (本体) — the pinned 「全局」 row at the top of the list.
  const rootTopic = computed<Topic | null>(() => source.topics.find((t) => inferTopicKind(t) === 'root') ?? null)

  /** 侧栏提示里说的那个队友的名字，不写死「芝士」。 */
  const agentName = computed(() => store.agentName)

  // 行的折叠开关 hover 那一句：收着的时候要说清里面有什么（展开之后就不必了）。
  function toggleTitle(row: VisibleRow<Topic>): string {
    if (!row.collapsed) return t('work.sidebar.collapse')
    if (row.hiddenStalled) return t('work.sidebar.expandStalled')
    if (row.hiddenAwaits) return t('work.sidebar.expandAwaits')
    if (row.hiddenWorking) return t('work.sidebar.expandWorking')
    return t('work.sidebar.expand')
  }

  return {
    // 树
    rootTopic,
    activeTree,
    railRows,
    railSections,
    selectedLocation,
    // 折叠
    toggleCollapse,
    // 状态
    topicById,
    unreadOf,
    freshOf,
    privateUnreadTotal,
    stalledOf,
    awaitsOf,
    workingOf,
    memberMarks,
    toggleTitle,
  }
}
