// 左侧房间列表的两件纯逻辑：**分组**（按"与我相关"切成两组）和**折叠**（有下级
// 的行可以收起来，「其他话题」整组也可以收起来）。
//
// TopicSidebar 的树是**拍平**的（`{topic, depth}` 的数组，DFS 顺序），折叠因此
// 也在拍平的数组上做：这里只负责「哪些行还看得见 / 收起来的行替谁背着未读」，
// 树怎么建、根话题不占行、孤儿兜底这些都留在组件里没动。
//
// 两条硬规则（收起来不能把信息吞掉）：
//   1. 当前选中的话题永远看得见——它的祖先链即使被收起来，通往它的那条路径
//      仍然渲染（`reveal`）。折叠开关的状态因此始终是用户自己设的那个值，不会
//      被导航偷偷改写。
//   2. 被收起来的后代的未读，冒到**离它最近的那个「看得见且收起来」的祖先**行
//      上（`unreadTotal`）。每个隐藏行只算一次，不会被两层祖先重复计。
//   3. 同样地，被收起来的后代里**成员的动静**（有队友在干活 / 有事等人处理）也要冒上来
//      （`hiddenWorking` / `hiddenAwaits`）。未读是数字要相加，状态是"有没有"
//      所以取或——父行的折叠开关凭它上色，"这里面有动静"才不会被折叠吞掉。

import { t } from '@/i18n'

export interface TopicNodeLike {
  id: string
  parent_id?: string | null
}

/** 判定一棵树里的行是哪一种话题时要看的那一个字段。 */
export interface TopicKindLike extends TopicNodeLike {
  kind?: string | null
}

/**
 * 这个话题是「本体」（root）还是普通话题（topic）。
 *
 * 后端大多数时候已经给了 `kind`；没给就从形状推——**有父话题的一定是话题**，
 * 没有父话题的那个就是本体。老载荷（`kind` 还没上线时存下来的）走的是后一半，
 * 所以这一层不是死代码。
 */
export function inferTopicKind(topic: TopicKindLike): string {
  const explicit = topic.kind
  if (typeof explicit === 'string' && explicit) return explicit
  return topic.parent_id ? 'topic' : 'root'
}

/** 边栏画的是房间。房间里派出去的活是**卡**，不是地点，所以这里只有两种。 */
const KIND_LABELS: Record<string, string> = {
  root: 'navigation.project.general',
  topic: 'work.sidebar.kind.topic',
}

/** 行尾那个种类词（已归档那一段平列表用）。 */
export function kindLabel(topic: TopicKindLike): string {
  return t(KIND_LABELS[inferTopicKind(topic)] ?? 'work.sidebar.kind.topic')
}

/** 拍平树的一行：话题 + 缩进深度（TopicSidebar 里的 `TreeRow`）。 */
export interface FlatRow<T extends TopicNodeLike = TopicNodeLike> {
  topic: T
  depth: number
}

/** 一行渲染所需的全部信息（折叠开关、收起来的条数、要显示的未读总数）。 */
export interface VisibleRow<T extends TopicNodeLike = TopicNodeLike> extends FlatRow<T> {
  /** 这行在当前（未归档）列表里有没有下级——没有就不画折叠开关。 */
  hasChildren: boolean
  /** 用户把这行收起来了。 */
  collapsed: boolean
  /** 因为这行收起来而没渲染的后代条数（不含被更深一层折叠重复计的）。 */
  hiddenCount: number
  /** 隐藏后代的未读之和——收起来时冒到本行上，避免"有新消息"被折叠吞掉。 */
  hiddenUnread: number
  /** 本行角标该显示的数字：自己的未读 + `hiddenUnread`。 */
  unreadTotal: number
  /** 隐藏后代里有没有成员正在干活的（一位 AI 队友有一轮在跑）。 */
  hiddenWorking: boolean
  /** 隐藏后代里有没有在等人处理的。 */
  hiddenAwaits: boolean
  /** 隐藏后代里有没有在等某位成员、而且等太久了的。 */
  hiddenStalled: boolean
}

/**
 * 未读角标上的那个数字：最多写到 99+。一个失控的计数不该把行撑开，而这条规则
 * 只跟「角标画得下几个字」有关，所以它和 `unreadTotal` 住在一起 —— 画角标的那
 * 几个组件（`components/topic-sidebar/*`）都从这儿取，不各写一份。
 */
export function countLabel(n: number): string {
  return n > 99 ? '99+' : String(n)
}

interface Node<T extends TopicNodeLike> {
  row: FlatRow<T>
  parent: Node<T> | null
  children: Node<T>[]
}

/**
 * 把拍平的行还原成父子结构。用 depth 单调栈，所以中间层被过滤掉（比如父话题
 * 已归档、子话题还活着）时，孙子会挂到最近的那个更浅的祖先上——跟拍平数组
 * 「后面所有 depth 更大的行都是我的后代」这个语义一致。
 */
function buildNodes<T extends TopicNodeLike>(rows: readonly FlatRow<T>[]): Node<T>[] {
  const roots: Node<T>[] = []
  const stack: Node<T>[] = []
  for (const row of rows) {
    while (stack.length > 0 && stack[stack.length - 1].row.depth >= row.depth) stack.pop()
    const parent = stack.length > 0 ? stack[stack.length - 1] : null
    const node: Node<T> = { row, parent, children: [] }
    if (parent) parent.children.push(node)
    else roots.push(node)
    stack.push(node)
  }
  return roots
}

/**
 * 选中话题 + 它的全部祖先的 id（含自己）。用 parent_id 往上走，带环保护——
 * 拍平树本身对环有兜底，这里也不能因为脏数据死循环。
 */
export function ancestorPathIds(topics: readonly TopicNodeLike[], selectedId: string | null | undefined): Set<string> {
  const path = new Set<string>()
  if (!selectedId) return path
  const byId = new Map<string, TopicNodeLike>()
  for (const t of topics) byId.set(t.id, t)
  let currentId: string | null = selectedId
  while (currentId !== null && !path.has(currentId)) {
    const node = byId.get(currentId)
    if (!node) break
    path.add(currentId)
    currentId = node.parent_id ?? null
  }
  return path
}

export interface VisibleRowsOptions {
  /** 用户收起来的话题 id。 */
  collapsed?: ReadonlySet<string>
  /** 无论祖先收没收起来都必须看得见的 id（= 当前选中话题的祖先链）。 */
  reveal?: ReadonlySet<string>
  /** 话题级未读；缺省当 0。 */
  unreadOf?: (id: string) => number
  /** 这个话题里有没有成员正在干活；缺省当否。 */
  workingOf?: (id: string) => boolean
  /** 这个话题是不是在等人处理；缺省当否。 */
  awaitsOf?: (id: string) => boolean
  /** 这个话题是不是在等某位成员、而且等太久了；缺省当否。 */
  stalledOf?: (id: string) => boolean
}

/**
 * 拍平树 → 实际要渲染的行。
 *
 * 一行渲染，当且仅当「没有任何被收起来的祖先」或「它在 reveal 里」。因为
 * reveal 是祖先闭包（`ancestorPathIds`），在 reveal 里的行其祖先也一定在，
 * 所以通往选中话题的整条路径永远完整可见。
 */
export function visibleRows<T extends TopicNodeLike>(
  rows: readonly FlatRow<T>[],
  options: VisibleRowsOptions = {}
): VisibleRow<T>[] {
  const collapsedIds = options.collapsed ?? new Set<string>()
  const reveal = options.reveal ?? new Set<string>()
  const unreadOf = options.unreadOf ?? (() => 0)
  const workingOf = options.workingOf ?? (() => false)
  const awaitsOf = options.awaitsOf ?? (() => false)
  const stalledOf = options.stalledOf ?? (() => false)

  const roots = buildNodes(rows)
  const rendered: Node<T>[] = []
  const renderedSet = new Set<Node<T>>()
  const hidden: Node<T>[] = []

  const walk = (node: Node<T>, underCollapsed: boolean) => {
    const isRendered = !underCollapsed || reveal.has(node.row.topic.id)
    if (isRendered) {
      rendered.push(node)
      renderedSet.add(node)
    } else {
      hidden.push(node)
    }
    const collapsedHere = node.children.length > 0 && collapsedIds.has(node.row.topic.id)
    for (const child of node.children) walk(child, underCollapsed || collapsedHere)
  }
  for (const root of roots) walk(root, false)

  // 每个隐藏行只记在「离它最近的、自己也看得见的、收起来的祖先」名下，
  // 这样嵌套折叠时同一条未读不会在两层父行上各显示一次。
  const owned = new Map<
    Node<T>,
    { count: number; unread: number; working: boolean; awaits: boolean; stalled: boolean }
  >()
  for (const node of hidden) {
    let owner: Node<T> | null = node.parent
    while (owner && !(renderedSet.has(owner) && collapsedIds.has(owner.row.topic.id))) owner = owner.parent
    if (!owner) continue
    const acc = owned.get(owner) ?? {
      count: 0,
      unread: 0,
      working: false,
      awaits: false,
      stalled: false,
    }
    acc.count += 1
    acc.unread += unreadOf(node.row.topic.id)
    acc.working = acc.working || workingOf(node.row.topic.id)
    acc.awaits = acc.awaits || awaitsOf(node.row.topic.id)
    acc.stalled = acc.stalled || stalledOf(node.row.topic.id)
    owned.set(owner, acc)
  }

  return rendered.map((node) => {
    const id = node.row.topic.id
    const hasChildren = node.children.length > 0
    const acc = owned.get(node)
    const hiddenUnread = acc?.unread ?? 0
    return {
      topic: node.row.topic,
      depth: node.row.depth,
      hasChildren,
      collapsed: hasChildren && collapsedIds.has(id),
      hiddenCount: acc?.count ?? 0,
      hiddenUnread,
      unreadTotal: unreadOf(id) + hiddenUnread,
      hiddenWorking: acc?.working ?? false,
      hiddenAwaits: acc?.awaits ?? false,
      hiddenStalled: acc?.stalled ?? false,
    }
  })
}

// ---- 折叠状态的持久化 ----
// 按项目一份，存**展开的** id。
//
// 以前存的是反过来的（收起来的 id，默认全展开），因为那时一个房间下面挂的是子
// 话题，藏起来就等于弄丢了一个人可能正在找的话题。现在挂的是这个房间派出去的
// 活，而活的去处是右边的 Task Progress —— 一个跑久了的房间有近两百条，全都摊在
// 主导航上，等于把侧栏变成一份没人读得完的清单。所以默认收起，展开是个动作。
//
// 键换了新的（`.v2`），不是加个版本号图好看：旧键里那批 id 的含义正好相反，照
// 旧读进来会把用户当初收起来的那几个房间变成唯一展开的那几个。
const EXPANDED_PREFIX = 'cheesex.topicExpanded.v2:'
function keyFor(prefix: string, projectId: string | null | undefined): string | null {
  const normalized = (projectId ?? '').trim()
  return normalized ? `${prefix}${encodeURIComponent(normalized)}` : null
}

function storageKey(projectId: string | null | undefined): string | null {
  return keyFor(EXPANDED_PREFIX, projectId)
}

export function loadExpandedTopics(projectId: string | null | undefined): Set<string> {
  const key = storageKey(projectId)
  if (!key || typeof localStorage === 'undefined') return new Set()
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      localStorage.removeItem(key)
      return new Set()
    }
    return new Set(parsed.filter((v): v is string => typeof v === 'string'))
  } catch {
    // 读坏了就回到默认的收起态。一条脏数据不该把两百行活摊到主导航上。
    return new Set()
  }
}

export function saveExpandedTopics(projectId: string | null | undefined, expanded: ReadonlySet<string>): void {
  const key = storageKey(projectId)
  if (!key || typeof localStorage === 'undefined') return
  try {
    if (expanded.size === 0) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify([...expanded]))
  } catch {
    // 隐私模式/配额满：展开仍然在内存里生效，只是这次刷新后不保留。
  }
}
