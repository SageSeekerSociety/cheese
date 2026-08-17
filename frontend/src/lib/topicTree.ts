// 左侧话题列表的两件纯逻辑：**分组**（按"与我相关"切成两组）和**折叠**（有子话题
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
//   3. 同样地，被收起来的后代的**状态**（芝士在跑 / 有事等人处理）也要冒上来
//      （`hiddenRunning` / `hiddenAwaits`）。未读是数字要相加，状态是"有没有"
//      所以取或——父行的折叠开关凭它上色，"这里面有动静"才不会被折叠吞掉。

export interface TopicNodeLike {
  id: string
  parent_id?: string | null
}

/** 分组判定用到的两个后端字段（`TopicOut`，正交布尔）。 */
export interface TopicRelevanceLike extends TopicNodeLike {
  /** 我在名册里 / 我建的 / 我是验收人 / 我被 @ 过，四者取一。 */
  i_participate?: boolean | null
  /** 现在正等我做事：点名给我的验收卡，或 @我 的未读。为真时 `i_participate` 必然为真。 */
  awaits_me?: boolean | null
}

/** 拍平树的一行：话题 + 缩进深度（TopicSidebar 里的 `TreeRow`）。 */
export interface FlatRow<T extends TopicNodeLike = TopicNodeLike> {
  topic: T
  depth: number
}

/** 一行渲染所需的全部信息（折叠开关、收起来的条数、要显示的未读总数）。 */
export interface VisibleRow<T extends TopicNodeLike = TopicNodeLike> extends FlatRow<T> {
  /** 这行在当前（未归档）列表里有没有子话题——没有就不画折叠开关。 */
  hasChildren: boolean
  /** 用户把这行收起来了。 */
  collapsed: boolean
  /** 因为这行收起来而没渲染的后代条数（不含被更深一层折叠重复计的）。 */
  hiddenCount: number
  /** 隐藏后代的未读之和——收起来时冒到本行上，避免"有新消息"被折叠吞掉。 */
  hiddenUnread: number
  /** 本行角标该显示的数字：自己的未读 + `hiddenUnread`。 */
  unreadTotal: number
  /** 隐藏后代里有没有正在跑的（芝士在里面工作）。 */
  hiddenRunning: boolean
  /** 隐藏后代里有没有在等人处理的。 */
  hiddenAwaits: boolean
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

// ---- 相关性分组 ----
// 侧栏把话题分成两组：「我参与的」平铺，「其他话题」收进一个默认折叠的组。
//
// 三条规则，每条都是为了"分组不能让人找不到东西"：
//   1. **整棵子树跟着它的根走。** 判定的单位是顶层话题连它底下所有子话题，而不是
//      单个话题——按单个话题分，一个父话题去了上组、它的子话题留在下组，缩进就
//      失去参照（depth 1 的行在自己组里没有 depth 0 的父行），竖向引导线也指向
//      一行不存在的东西。子树里只要有一个话题与我相关，整棵就上去；父话题因此
//      顺带出现在上组，那正是"通往它的那条路径"。
//   2. **`awaits_me` 永远在上组。** 它是"等我做事"，藏在折叠组里就等于没通知。
//      后端保证 `awaits_me` 为真时 `i_participate` 必然为真，所以规则 1 已经覆盖
//      它；判定里仍然把它单独写出来，是为了万一后端语义变了也不会把它折进去。
//   3. **字段缺失当"相关"，只有明确说了 `false` 才降级。** 老客户端、没经过
//      `list_topics` 的载荷、以及任何还没算过这两个字段的地方给的都是 undefined；
//      那种时候宁可全部照旧显示，也不要把整个项目的话题静默折起来。

/** 分组结果：两组都仍然是拍平的 `{topic, depth}` 数组，可以直接喂给 `visibleRows`。 */
export interface RelevancePartition<T extends TopicNodeLike = TopicNodeLike> {
  /** 与我相关（含"子树里有相关话题"而被带上来的祖先）。 */
  mine: FlatRow<T>[]
  /** 整棵子树都与我无关。 */
  others: FlatRow<T>[]
}

/** 这个话题算不算"与我相关"。见上面规则 2、3。 */
export function isMyTopic(topic: TopicRelevanceLike): boolean {
  return topic.awaits_me === true || topic.i_participate !== false
}

function collectSubtree<T extends TopicNodeLike>(
  node: Node<T>,
  out: FlatRow<T>[],
  isMine: (topic: T) => boolean
): boolean {
  out.push(node.row)
  let relevant = isMine(node.row.topic)
  // 不短路：整棵子树都要进 out，判定只是顺带求个"或"。
  for (const child of node.children) {
    if (collectSubtree(child, out, isMine)) relevant = true
  }
  return relevant
}

/**
 * 拍平树 → 按相关性切成两组，各组内部保持原来的顺序和 depth。
 *
 * 顶层单位是 `buildNodes` 认定的根（= 前面没有更浅的行），所以中间层被过滤掉的
 * 情形——父话题已归档、子话题还活着——和 `visibleRows` 的处理完全一致。
 */
export function partitionByRelevance<T extends TopicRelevanceLike>(
  rows: readonly FlatRow<T>[],
  isMine: (topic: T) => boolean = isMyTopic
): RelevancePartition<T> {
  const mine: FlatRow<T>[] = []
  const others: FlatRow<T>[] = []
  for (const root of buildNodes(rows)) {
    const subtree: FlatRow<T>[] = []
    const target = collectSubtree(root, subtree, isMine) ? mine : others
    for (const row of subtree) target.push(row)
  }
  return { mine, others }
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
  /** 这个话题里芝士是不是正在跑；缺省当否。 */
  runningOf?: (id: string) => boolean
  /** 这个话题是不是在等人处理；缺省当否。 */
  awaitsOf?: (id: string) => boolean
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
  const runningOf = options.runningOf ?? (() => false)
  const awaitsOf = options.awaitsOf ?? (() => false)

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
  const owned = new Map<Node<T>, { count: number; unread: number; running: boolean; awaits: boolean }>()
  for (const node of hidden) {
    let owner: Node<T> | null = node.parent
    while (owner && !(renderedSet.has(owner) && collapsedIds.has(owner.row.topic.id))) owner = owner.parent
    if (!owner) continue
    const acc = owned.get(owner) ?? { count: 0, unread: 0, running: false, awaits: false }
    acc.count += 1
    acc.unread += unreadOf(node.row.topic.id)
    acc.running = acc.running || runningOf(node.row.topic.id)
    acc.awaits = acc.awaits || awaitsOf(node.row.topic.id)
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
      hiddenRunning: acc?.running ?? false,
      hiddenAwaits: acc?.awaits ?? false,
    }
  })
}

// ---- 折叠状态的持久化 ----
// 按项目一份，存**收起来的** id（不是展开的）：默认展开，新话题天然可见，
// 存量数据也不会因为多了个话题就把它藏起来。

const COLLAPSE_PREFIX = 'cheesex.topicCollapsed.v1:'
// 「其他话题」组展开没展开，也按项目一份。和上面共用同一套键格式，只是存的东西
// 反过来：这一组**默认折叠**，所以键存在 = 用户展开过，键不在 = 默认的折叠态。
// （折叠集合那边默认展开，存的是"收起来的 id"，同样是让默认态等于键不存在。）
const OTHERS_OPEN_PREFIX = 'cheesex.railOthersOpen.v1:'

function keyFor(prefix: string, projectId: string | null | undefined): string | null {
  const normalized = (projectId ?? '').trim()
  return normalized ? `${prefix}${encodeURIComponent(normalized)}` : null
}

function storageKey(projectId: string | null | undefined): string | null {
  return keyFor(COLLAPSE_PREFIX, projectId)
}

export function loadOthersGroupOpen(projectId: string | null | undefined): boolean {
  const key = keyFor(OTHERS_OPEN_PREFIX, projectId)
  if (!key || typeof localStorage === 'undefined') return false
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

export function saveOthersGroupOpen(projectId: string | null | undefined, open: boolean): void {
  const key = keyFor(OTHERS_OPEN_PREFIX, projectId)
  if (!key || typeof localStorage === 'undefined') return
  try {
    if (open) localStorage.setItem(key, '1')
    else localStorage.removeItem(key)
  } catch {
    // 隐私模式/配额满：展开仍然在内存里生效，只是这次刷新后不保留。
  }
}

export function loadCollapsedTopics(projectId: string | null | undefined): Set<string> {
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
    // 读坏了就当没折叠过——宁可多显示，也不要因为一条脏数据让人看不到话题。
    return new Set()
  }
}

export function saveCollapsedTopics(projectId: string | null | undefined, collapsed: ReadonlySet<string>): void {
  const key = storageKey(projectId)
  if (!key || typeof localStorage === 'undefined') return
  try {
    if (collapsed.size === 0) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify([...collapsed]))
  } catch {
    // 隐私模式/配额满：折叠仍然在内存里生效，只是这次刷新后不保留。
  }
}
