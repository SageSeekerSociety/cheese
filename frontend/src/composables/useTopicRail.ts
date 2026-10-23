// 项目侧栏（`TopicSidebar`）那一半「状态」：我加入了的频道、谁收着、收起来的那些行
// 把未读和成员的动静交给谁、以及那只让红标自己亮起来的慢钟。
//
// 和画的那一半分家的理由，和 #2158 拆 PanelPreview 是同一条：这些东西原先长在
// 那个 1887 行的组件里，于是「折叠记不记得住」「红灯会不会自己亮」只能连着整条
// 侧栏一起测。搬到这里之后，它们各自是一条能单独说的线，展示组件只认 props。
//
// **这一半不认识路由**：跳转、「我在哪」、行的 ⋯ 里那几项（要 router 才算得出链接）
// 在 `useTopicRailRoutes.ts`。分开是为了让这一半能在一个没有路由的宿主里跑起来。
import type { RailTask } from '../components/topic-sidebar/moreTasks'
import type { Topic } from '../cx_types'
import type { RailMemberMark } from '../lib/memberActivity'
import type { RowLight } from '../lib/roomLight'
import type { TaskState } from '../lib/taskState'
import type { FlatRow, VisibleRow } from '../lib/topicTree'
import type { TopicUnread } from '../types/channels'

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { agentIdentities, agentNames } from '../lib/agentNames'
import { isAgentHandle } from '../lib/authorship'
import { waitStalled } from '../lib/replyWait'
import { lightOf as lightFor } from '../lib/roomLight'
import { taskState } from '../lib/taskState'
import { ancestorPathIds, inferTopicKind, loadExpandedTopics, saveExpandedTopics, visibleRows } from '../lib/topicTree'
import { VIRTUAL_LIST_THRESHOLD } from '../lib/virtualList'
import { getAvatarUrl } from '../utils/materials'

import { t } from '@/i18n'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

/** 侧栏要画的东西全在父级的这几个 props 里；这一层不自己取数。 */
export interface TopicRailSource {
  topics: Topic[]
  selectedProjectId: string | null
  selectedTopicId: string | null
  /** 正打开的东西在哪个频道里：频道本身、它的一件任务、它的「全部任务」。没加入的频道
   *  也照样留在侧栏里的，是这一个。缺省时就是选中的那个频道。 */
  hereChannelId?: string | null
  /** 频道和任务在等我的东西：{id: TopicUnread}，缺键 = 没有。 */
  unreadMap?: Record<string, TopicUnread>
  /** 私聊未读：{peerHandle: count}。侧栏只用它的总数。 */
  privateUnreadMap?: Record<string, number>
  /** 挂在频道下面的活（频道 id → 那几条），侧栏任务行的灯按它们各自的四样事实算。 */
  roomTasks?: Record<string, RailTask[]>
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
      // 侧栏只列我加入了的频道，外加我此刻正在里面的那一个（从「全部频道」点进来、还没
      // 加入的）——不然人就不知道自己在哪儿。打开它的一件任务也还在它里面：顶栏写着
      // 「# 频道 / 任务」，侧栏不能在这时把那个频道收走。已归档的频道不在侧栏里，在项目设置的
      // 「频道」一栏。`joined` 缺席（没经过 list_topics 的载荷）当作加入了：宁可多列，
      // 不把频道静默藏起来。
      if (row.topic.status === 'archived') continue
      if (row.topic.joined === false && row.topic.id !== (source.hereChannelId ?? source.selectedTopicId)) continue
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
  // 折叠聚合要按 id 问「这一行此刻画哪盏灯」，而拍平树里只留了 id。走一遍 props.topics
  // 建索引，别在每一行上做线性查找。
  //
  // 房间自己没有状态：问的都是房间里的成员。侧栏只画在干活的队友，不画打字的人——
  // 这份列表隔一阵才读一次，而打字几秒就过去了，画出来多半已经不是真的了。
  const topicById = computed(() => new Map(source.topics.map((t) => [t.id, t])))
  function workersOf(topic: Topic | undefined) {
    return (topic?.activity ?? []).filter((a) => a.kind === 'working')
  }

  // 灯要跟着钟亮：列表三十秒才刷一次，而「等满五分钟」是时间自己走到的，不是
  // 数据变出来的。所以这里自己有一只慢钟，每 10 秒拨一下让判断重算。
  const clock = ref(Date.now())
  let clockTimer: number | undefined
  onMounted(() => {
    clockTimer = window.setInterval(() => (clock.value = Date.now()), 10_000)
  })
  onUnmounted(() => {
    if (clockTimer !== undefined) window.clearInterval(clockTimer)
  })

  // ---- 灯 ----
  // 状态那几盏的判据只写在 `lib/roomLight.ts` 一处；这一层只把后端给的事实凑齐，再带
  // 上这只钟。频道行和任务行凑的是同一份事实，所以两边的灯说的是同一件事 —— 只有
  // 「有人回复了我」是频道行单独一样（`replied_to_me`）：活有它自己那一行，那件事
  // 后端只给频道算。
  //
  // 「点开过就灭」的长绿不在这里判：那由数据决定（服务端按已读位算 `unseen_delivery`，
  // 点开时 `stores/workspace` 先把本地那一份灭掉）。呼吸绿问的 `moving` 也一样，
  // 后端按会话状态和交付链路算好；这一层只查表。
  //
  // 蓝灯（我负责的活）是这一层唯一自己下的判断：它不看状态，只看这条活归谁——而且要
  // 状态那几盏都没话说的时候才画。
  function lightOf(id: string): RowLight | null {
    const topic = topicById.value.get(id)
    if (!topic) return null
    return lightFor(
      {
        awaitsMe: topic.awaits_me === true,
        repliedToMe: topic.replied_to_me === true,
        // 房间里、支线里、名下的活里有会话在跑，或者有活走在交付链路上：呼吸绿。
        moving: topic.moving === true,
      },
      clock.value
    )
  }

  /** 挂在频道下面的一条活。和频道行同一个判据，只是事实来自这条活自己那一份 ——
   *  只保留「有事等我（黄灯）」和「正在跑（呼吸绿）」。任务行自己不画呼吸绿（见
   *  `taskRowLight`），它只在收起来的那一堆里替它们说「有任务正在进行」。 */
  function taskLight(task: RailTask, { moving = true }: { moving?: boolean } = {}): RowLight | null {
    return lightFor(
      {
        awaitsMe: task.awaits_me === true,
        repliedToMe: false,
        // 这条活的会话在跑，或者它走在交付链路上：呼吸绿。
        moving: moving && task.moving === true,
        bounced: task.presentation?.phrase === 'bounced',
      },
      clock.value
    )
  }

  /** 任务行行尾那颗灯：不画呼吸绿。任务行上「在做」的图标已经在转，同一行只留一样
   *  在动的东西（design-system §9.0）。 */
  function taskRowLight(task: RailTask): RowLight | null {
    return taskLight(task, { moving: false })
  }

  /** 挂在频道下面的一条活的状态记号和它下面那一行（`lib/taskState.ts`），和全部任务、
   *  任务页头同一个样子。这份行没有 `session_running`，「在做」转不转看 `moving`。 */
  function taskStateOf(task: RailTask): TaskState {
    const agent = task.agent_handle ? agentNameMap.value.get(task.agent_handle) : undefined
    return taskState(task, {
      viewer: myHandle(),
      nameOf: (handle) => memberOf(handle).name || handle,
      agentName: agent || agentName.value,
      now: clock.value,
    })
  }

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

  /** 这一行上画的那几位成员：此刻在这里干活的（绿点）。房间在等一位队友、等太久了
   *  是这一行的**状态**，由行尾那盏红灯说（`lib/roomLight.ts` 的 `waiting`），不再画
   *  成头像——和任务行同一个规矩。同一位只画一次；卡住了的那位也不画（红灯已经说了，
   *  头像上再点一颗绿点会和它打架）。 */
  function memberMarks(topic: Topic): RailMemberMark[] {
    const marks: RailMemberMark[] = []
    const drawn = new Set<string>()
    const add = (who: RailMemberWho, title: string) => {
      if (drawn.has(who.identity)) return
      drawn.add(who.identity)
      marks.push({
        handle: who.handle,
        name: who.name,
        avatar: who.avatar,
        agent: who.agent,
        title,
      })
    }
    const stalled = new Set(stalledWaits(topic).map((wait) => memberOf(wait.member).identity))
    for (const work of workersOf(topic)) {
      const who = memberOf(work.member)
      if (stalled.has(who.identity)) continue
      add(who, t('work.sidebar.workingTip', { name: who.name }))
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
      lightOf,
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

  // 行的折叠开关 hover 那一句：收着的时候要说清**里面**有什么 —— 开关说的是展开
  // 这件事，所以只看收起来的后代，这一行自己的灯在行尾那颗点上。
  function toggleTitle(row: VisibleRow<Topic>): string {
    if (!row.collapsed) return t('work.sidebar.collapse')
    if (row.hiddenStalled) return t('work.sidebar.expandStalled')
    const hidden = row.hiddenLight
    if (hidden?.kind === 'red') {
      // 收起来的一堆里最亮的那盏红，可能是「停下报错没人接手」，也可能是「在等一位
      // 队友等太久了」（`RowLight.why` 记着是哪一件，见 `lib/roomLight.ts`）。
      return t(hidden.why === 'unfinished' ? 'work.sidebar.expandStopped' : 'work.sidebar.expandStalled')
    }
    if (hidden?.kind === 'yellow') return t('work.sidebar.expandAwaits')
    if (hidden?.kind === 'green') {
      return t(hidden.breathe ? 'work.sidebar.expandWorking' : 'work.sidebar.expandDelivered')
    }
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
    lightOf,
    taskLight,
    taskRowLight,
    taskStateOf,
    memberMarks,
    toggleTitle,
  }
}
