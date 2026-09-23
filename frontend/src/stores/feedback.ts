/**
 * stores/feedback.ts — 反馈功能的**全部状态**，接真接口。
 *
 * 它是这一层存在的唯一理由：页面和组件一律通过 action / getter 读写，谁都不许自己
 * 发请求。上一轮它是纯内存的原型（动作同步改本地数组），这一轮函数体换成了网络调用，
 * 而**调用方几乎没有改** —— 那正是当初把它单独拆出来的目的。
 *
 * 三件不再由前端决定的事：
 *
 *   1. **权限**。「我是不是管理员」读服务端的 `GET /feedback/meta`（`meta.is_admin`），
 *      不再是一个可以拨的开关。上一轮那个「原型身份」开关整块删掉了 —— 它当时就写着
 *      「不是权限门」，现在门在服务端，客户端连猜都不该猜。
 *   2. **隐私**。私密条目由服务端 `WHERE` 决定谁能看见（提交者 ∪ 管理员 ∪ 提出它的房间），前端不做
 *      第二遍过滤：客户端过滤只能挡住界面，挡不住链接。上一轮那句「将来这层整个删掉」
 *      说的就是现在。
 *   3. **有哪些状态、按什么顺序走**。词表来自 `GET /feedback/meta`；颜色留在
 *      `lib/feedbackMeta.ts`（那是视觉决定，服务端不该知道 `--warn-wash`）。
 *
 * 列表的 Tab 和搜索都是**服务端**参数，所以切 Tab / 改搜索词会重新拉一页，而不是在
 * 本地数组上 filter。搜索键击用 250ms 防抖，而且只有最新一次请求的结果会被采纳
 * （见下面那几个模块级计数器），否则慢的那次后到，会把快的那次覆盖掉。
 */

import type { FeedbackAdminPatch, FeedbackListQuery, StatsKind, StatsShapes } from '@/api'
import type {
  FeedbackCard,
  FeedbackComment,
  FeedbackCounts,
  FeedbackCreateBody,
  FeedbackDetail,
  FeedbackKind,
  FeedbackMeta,
  FeedbackPriority,
  FeedbackProposal,
  FeedbackStatus,
  FeedbackVisibility,
} from '@/cx_types'

import { defineStore } from 'pinia'

import { ApiError } from '@/api'
import {
  acceptFeedbackProposal,
  createAdminFeedbackNote,
  createFeedback,
  createFeedbackComment,
  deleteFeedback,
  deleteFeedbackComment,
  dismissFeedbackProposal,
  getAdminFeedback as getAdminFeedbackDetail,
  getFeedback,
  getFeedbackCounts,
  getFeedbackMeta,
  getStats,
  likeFeedbackComment,
  listAdminFeedback,
  listFeedback,
  listFeedbackComments,
  listFeedbackProposals,
  listMyFeedback,
  markFeedbackRead,
  patchAdminFeedback,
  setAdminFeedbackStatus,
  supportFeedback,
  unlikeFeedbackComment,
  unsupportFeedback,
} from '@/api'
import {
  clearLiveFeedbackDraft,
  forgetFeedbackDraft,
  isDraftMeaningful,
  loadFeedbackDraft,
  parkFeedbackDraft,
  saveFeedbackDraft,
  takeParkedFeedbackDraft,
} from '@/lib/feedbackDraft'
import { STATUS_LADDER } from '@/lib/feedbackMeta'

/** 公开列表的栏位。服务端 `PUBLIC_TABS` 是**同一个集合**：加一个栏位是后端改
 *  一处、前端跟着改一处类型的事。 */
export type FeedbackTab = 'all' | 'hot' | 'active' | 'resolved'
/** 管理端的栏位。同上，服务端 `ADMIN_TABS`。 */
export type AdminTab = 'public' | 'private' | 'agent' | 'security'
/** 列表的排序。服务端 `SORTS` —— `new` 按时间倒序，`supports` 按支持数。
 *  管理台那个「最新 / 最热」切换就是这个类型的两半。 */
export type AdminSort = 'new' | 'supports'

/** 搜索防抖，毫秒。250 是人停手和「它没反应」之间的那条线。 */
const SEARCH_DEBOUNCE_MS = 250
/** 一页拉多少条。**列表有「加载更多」了，但这一页仍然给 50**：一次拿得宽一点，
 *  多数列表（这个平台上百来条反馈撑死）一页就到底，第一屏不必再等第二次往返；
 *  真长起来的那几栏才走翻页。 */
const PAGE_SIZE = 50
/** 表单落盘的防抖，毫秒。比搜索那条长一点：写的是本机磁盘，不是网络，而中文输入法
 *  在候选阶段就会发 `update:model-value`，250 的话一次输入要写好几遍。 */
const DRAFT_SAVE_DEBOUNCE_MS = 400

/** 请求序号，用来丢掉过期响应。它们**不在 state 里**：一个自增的数字和定时器 id
 *  都不是界面状态，放进 state 只会让 Vue 去追踪一个没人渲染的值。
 *
 *  每个序号只管**自己那条路**（这一个列表的先后两次请求谁算数）。计数那条路没有
 *  属于自己的序号 —— 它跨路，用的是下面 `countsRev` 那一把。 */
let listSeq = 0
let adminSeq = 0
let mineSeq = 0
/** 看板那三类的代次，**一类一把**。
 *
 *  一把共用的计数器在这里是错的，而且错得很安静：看板挂载时就发了一次请求，人接着切
 *  到另一类 —— 共用计数器一自增，那一份还在飞的响应就被判成「过期」，于是第一类永远
 *  停在 `null`，切回去还要再拉一次。而「过期」在这里的**真实含义**只是「后来又问了同
 *  一类」，切到别的类并没有让谁过期。 */
const statsSeq: Record<StatsKind, number> = {
  feedback: 0,
  usage: 0,
  platform: 0,
  performance: 0,
  pipeline: 0,
  product: 0,
  integrations: 0,
}
/** 管理端那一条详情的代次。它的两次操作会在**同一个 id** 上相遇（读一次、写完再回一次），
 *  所以「id 一样」不足以判断一份响应还算不算数 —— 见 `loadAdminDetail` 与 `_adminWrite`。 */
let adminDetailSeq = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null
/** 表单落盘的防抖定时器，见 `touchDraft`。 */
let draftTimer: ReturnType<typeof setTimeout> | null = null
/** 管理台搜索框那一个。和 `searchTimer` 分开，因为两个框可能在同一个浏览器标签
 *  页里先后被用过（先进反馈中心搜一下，再进管理台搜一下）—— 共用一支定时器时，
 *  后者会把前者还没发出去的那次输入取消掉，而那次输入属于另一页。 */
let adminQueryTimer: ReturnType<typeof setTimeout> | null = null
/** 已经推过未读游标的 id。**不在 state 里**（同上面那几个序号）：它决定的是「这次
 *  还要不要发请求」，不是一个被渲染的值，放进 state 只会让 Vue 去追踪一个没人看的
 *  集合。 */
const markedReadIds = new Set<string>()

/** 计数的一把**单调修订号**，全仓库只有它一个来源。
 *
 *  `counts` 有好几条路会写：公开列表、管理端列表、`/feedback/counts` 那个轻量接口，
 *  以及两个**本地动作**（推已读游标）。它们发出的时间有先后，回来的时间却没有 ——
 *  先发的后到，屏幕上的数字就会从一个较新的值跳回一个较旧的。所以每个想提交
 *  counts 的人在**发出请求那一刻**领一个号，回来时带着它；`_commitCounts` 只认号
 *  大的。这样「谁算数」只由发出去的时刻决定，与到达顺序无关。
 *
 *  和上面那几个序号分开：那些是**每条路自己**的（同一个列表的先后两次请求谁算
 *  数），这一把是**跨路**的（列表的响应和推游标谁算数）。混成一把是错的：一次
 *  `loadList` 会把之后发出的 `refreshCounts` 一起顶掉。 */
let countsRev = 0
const takeCountsRev = (): number => ++countsRev

/** 公开列表上一次的那一页，连同它的**问题指纹**（Tab + 搜索词）。指纹命中就是
 *  「问的还是同一个问题」，那就先把手上这份画出来、背后再重拉一次 —— 骨架是「我还
 *  不知道」的表示，而这里明明知道，从详情页返回时不该再闪一次。
 *
 *  `counts` **不许进缓存**（见 `_commitCounts`）：一个存下来的数字没法回答「它现在
 *  还是不是真的」，把上一分钟的未读数再画一遍，比先空着更糟。 */
let listCache: { key: string; items: FeedbackCard[]; total: number } | null = null

/** 「我的反馈」那一页。它没有参数，所以指纹就是它自己。
 *
 *  这一份要**跟着身份走**：它装的是「我提的 / 指派给我的」，换个人登录之后留着就是
 *  上一个人的清单。`resetFeedbackCaches()` 负责，别在别处再清一遍。 */
let mineCache: { items: FeedbackCard[]; total: number } | null = null

/** 详情缓存：id → 上一次从 `GET /feedback/{id}` 拿到的那条。
 *
 *  **只让用户侧那一条路碰它**。管理端的 `loadAdminDetail` 回的是同一个类型，可见性
 *  判据却是另一个（管理员 vs 并集），把它的响应写进来就等于让同一台机器上的下一个
 *  读者先画出一份只有管理员能看的内容 —— 那不是「旧了一点」。
 *
 *  存的是**和 `state.detail` 同一个对象**：`_patch` 就地改字段（点赞、支持数）时，
 *  缓存里那份一起跟着变，不需要任何写回。反过来说，谁把 `state.detail` **整个换成
 *  另一个对象**（`loadDetail` 的响应、`_adminWrite` 的响应），就得自己决定要不要
 *  更新缓存 —— 见这两处的注释。 */
const detailCache = new Map<string, FeedbackDetail>()

/** 丢掉两份列表缓存。**不动已经在屏幕上的数组** —— 「屏幕上那份要不要清」是另一个
 *  决定（`submit` 清了，因为它刚插进去一条），这里只回答「下次还信不信缓存」。
 *  两件事分开写，是因为它们真的会分开：提交之后屏幕上那份该清，而缓存里那份对
 *  同一个查询仍在的回答是错的。 */
function dropListCaches(): void {
  listCache = null
  mineCache = null
}

/** 把这一层替**上一个人**记着的东西全部丢掉。退出登录、以及换成另一个人登录时调
 *  （见 `services/account.ts`）。
 *
 *  公开列表的那一份也要丢：私密反馈不在里面，但「这台机器上刚刚翻过哪一页反馈」
 *  仍然是那个人的痕迹，而 `/feedback` 对谁都是同一页。
 *
 *  **为什么清缓存就够了，`state` 不用一起清**：三个页面都把内容挂在 loading 那道
 *  门后面（`<LoadingSkeleton v-if="…loading">` + `v-else` 才是内容，见
 *  FeedbackCenterPage / FeedbackMinePage / FeedbackDetailPage）。`state` 里那份旧
 *  的在下一次挂载时会被自己那次请求翻成 loading=true、当场被骨架盖住；真正会把旧
 *  内容**画出来**的只有命中缓存这一条路 —— 它把 loading 留在 false 上，于是骨架让
 *  开、旧内容直接上屏，一直挂到重拉回来。所以这一关要卡在缓存上。 */
export function resetFeedbackCaches(): void {
  dropListCaches()
  detailCache.clear()
  // 这一份也要丢，理由和上面两份一样，只是它的内容是**谁**而不是**什么**：
  // `markedReadIds` 说的是「这个人的自动已读已经替这几条推过游标了」，留到换人之后
  // 就是拿上一个人的账本记下一个人 —— 下一个人点开同一条时那次自动已读会被静默跳过
  // （不推游标、不清徽标，也没有任何提示）。
  markedReadIds.clear()
}

const EMPTY_COUNTS: FeedbackCounts = { all: 0, hot: 0, active: 0, resolved: 0, unread: 0 }

/** 提交表单里那一份**内容**。三个入口共用它：反馈中心和「我的反馈」走独立页面
 *  （`/feedback/new`），会话里那张提案卡走对话框，而字段只有这一份
 *  （`SubmitFeedbackForm` 的两个壳）—— 各写一遍的话，「可见范围」这种后来才加的字段
 *  必然只会加进其中一份。
 *
 *  **「表单开着没有」不在这里**：壳自己知道自己在不在屏幕上（路由在不在、对话框开不
 *  开），store 里再留一个布尔就是同一个事实的第二份拷贝。 */
export interface FeedbackDraft {
  kind: FeedbackKind
  title: string
  body: string
  /** 「怎么重现」。只有 bug 那一类会问（见 `REPRO_KINDS`）—— 建议类要的是
   *  「你希望它变成什么样」，问它「怎么重现」是在问一个不存在的东西。 */
  repro: string
  /** 「你以为会发生什么」。bug 和建议都问：一句话就能把「哪里不对」和
   *  「你想让它怎样」分开，而这两件事在正文里常常混成一段读不出要求的话。 */
  expectation: string
  /** 标签。后端 `FeedbackCreate.tags` 一直收（`list[str]`，上限 20），卡片也一直在
   *  渲染 `item.tags`，只是表单以前没做。用来横向归类（登录 / 移动端 / 性能）。 */
  tags: string[]
  /** 「附带现场」：把 `fromAgent` 那三段会话信息一起提交。 */
  attachContext: boolean
  visibility: FeedbackVisibility
  /** Agent 发现的那条带过来的现场，勾了 `attachContext` 才随反馈一起提交。 */
  fromAgent?: {
    whatHappened: string
    repro: string
    evidence: string
    sessionId?: string
    environment?: string
  }
  /** 这一份是从哪张提案卡打开的。有值走「发送」那条路（`accept`），
   *  没有就是人自己新提一条。 */
  proposal?: { topicId: string; blockId: string }
}

/** 哪几类反馈要问「怎么重现」/「你以为会发生什么」。**按类型分栏的口径写在
 *  这一个地方**：表单问什么、请求体带什么、以及以后按类型筛列表，三处问的都是
 *  同一件「这类反馈该有哪些字段」；各写一遍的话，症状是「建议类提交上去的
 *  `repro` 永远是空的」这种谁也看不出来的偏差。 */
export const REPRO_KINDS: FeedbackKind[] = ['bug']
export const EXPECTATION_KINDS: FeedbackKind[] = ['bug', 'suggestion']

/** 一条反馈能带多少个标签。后端的 `max_length=20` 是同一件事（Pydantic 在 list 上
 *  数的是**条数**），两处写同一个数是刻意的：客户端先截，用户就在输入框边上看到上限，
 *  而不是写完正文才被服务端 422 挡回来。 */
export const MAX_TAGS = 20

/** 标签的规范化：去空白、丢空串、去重、截到 20 条。
 *
 *  后端的 `max_length=20` 只管条数，**不去重也不裁空白** —— 把这三件事放在客户端，
 *  是因为它们在人还在打字时就有答案；全都留给服务端，代价是「标签填了、提交被拒、
 *  正文跟着丢一次」这类最贵的那种失败。 */
export function cleanTags(tags: readonly string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of tags) {
    const tag = raw.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    out.push(tag)
    if (out.length >= MAX_TAGS) break
  }
  return out
}

function emptyDraft(): FeedbackDraft {
  return {
    kind: 'bug',
    title: '',
    body: '',
    repro: '',
    expectation: '',
    tags: [],
    attachContext: false,
    visibility: 'public',
  }
}

/** 看板那三份数据各存各的：分类 → 它那一份（还没拉到就是 null）。 */
type StatsBucket = { [K in StatsKind]: StatsShapes[K] | null }

/** 把刚拉到的那一份写进它自己那一格。
 *
 *  为什么不直接 `bucket[kind] = value`：那是**联合索引写入**，TS 没法保证写进去的正是
 *  那一格要的类型（三格的形状互不相同），它会拒绝。三个分支各自窄化一次，是对同一件
 *  事的显式说法 —— 也正是「切到用量却把反馈的数写进用量那一格」这类错会藏身的地方。 */
function assignStats(bucket: StatsBucket, kind: StatsKind, value: StatsShapes[StatsKind]): void {
  if (kind === 'feedback') bucket.feedback = value as StatsShapes['feedback']
  else if (kind === 'usage') bucket.usage = value as StatsShapes['usage']
  else if (kind === 'platform') bucket.platform = value as StatsShapes['platform']
  else if (kind === 'pipeline') bucket.pipeline = value as StatsShapes['pipeline']
  else if (kind === 'product') bucket.product = value as StatsShapes['product']
  else if (kind === 'integrations') bucket.integrations = value as StatsShapes['integrations']
  else bucket.performance = value as StatsShapes['performance']
}

/** 把表单折成请求体。**只有这一个地方做这件事**：两条提交路走的是同一个动作，
 *  各折一次的话，「可见范围」这种后来加的字段必然只会加进其中一份。 */
function toCreateBody(draft: FeedbackDraft): FeedbackCreateBody {
  const body = draft.body.trim()
  const agent = draft.attachContext ? draft.fromAgent : undefined
  // 按类型收进来的那两栏。**没问过的那一类一律不带**：表单上没出现过的字段，值只
  // 可能来自「上一次换了类型之前」（选过 bug、填了重现、又改成建议），把它一起发
  // 上去就是替用户声明了一件他没说过的事。
  const repro = REPRO_KINDS.includes(draft.kind) ? draft.repro.trim() : ''
  const expectation = EXPECTATION_KINDS.includes(draft.kind) ? draft.expectation.trim() : ''
  return {
    kind: draft.kind,
    title: draft.title.trim(),
    // 摘要默认取正文第一行：它在列表里是给人扫的那一句，空着等于列表里有一条空白。
    summary: body.split('\n')[0]?.slice(0, 120) || draft.title.trim(),
    problem: body,
    visibility: draft.visibility,
    tags: cleanTags(draft.tags),
    // 人自己写的那一栏优先，agent 带的现场只在人没写时补上 —— 现场是跟着提案卡
    // 一起过来的，而这一栏人刚才就看着它（bug 那一类表单项里有），他改过就该算他的。
    repro: repro || agent?.repro || null,
    expectation: expectation || null,
    what_happened: agent?.whatHappened ?? null,
    evidence: agent?.evidence ?? null,
    session_id: agent?.sessionId ?? null,
    environment: agent?.environment ?? null,
  }
}

/** 后端把能读的原因写在 `message` 里（`ApiError` 会把它带上），照它显示 ——
 *  「HTTP 412 for /feedback/...」会让人去查一个服务端早就解释过的东西。 */
function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

/** 把新到的一页接在手上这份清单后面。
 *
 *  **必须按 id 去重**，因为 `page_start` 是**偏移量**、不是游标：两次请求之间有人提了
 *  一条新的，它就插到队首，于是第 2 页从原来的第 50 条**前一条**开始 —— 屏幕上第 50
 *  条出现两遍。这不是排序没写全的锅（服务端三个排序都有 `display_no` 兜底，任何两条
 *  都比得出先后），是偏移量本身的性质：它数的是**位置**，而位置会动。
 *
 *  重复的代价不只是「多一条」：`:key` 撞了之后 Vue 会复用同 key 的节点，于是「多一条」
 *  和「少一条」同时发生 —— 那张卡片上显示的可能根本是另一条反馈的内容。
 *
 *  去重按 id（服务端的 uuid），不按标题：同一个标题下真的可以有几条不同的反馈。
 *  两份列表（公开 / 我的）共用这一个函数，是因为这里写错的表现是「某一页多一条」，
 *  两边各写一遍时这种错只会出现在其中一边。 */
function appendPage(existing: FeedbackCard[], page: FeedbackCard[]): FeedbackCard[] {
  if (!page.length) return existing
  const seen = new Set(existing.map((card) => card.id))
  const fresh = page.filter((card) => !seen.has(card.id))
  // 一条新的都没有时**原样返回那个数组**：换一个新数组会让整片列表重新渲染一遍，
  // 而这一次翻页什么都没带来。
  return fresh.length ? [...existing, ...fresh] : existing
}

export const useFeedbackStore = defineStore('feedback', {
  state: () => ({
    /* ---- 词表 ---- */
    meta: null as FeedbackMeta | null,
    /** 「问过服务端了」—— 成功也好失败也好，问过就是问过。页面用它区分「还不知道
     *  我是不是管理员」和「确定不是」：少了它，meta 还在飞的那一帧画出来的是
     *  「你的账号不在管理员名单里」，一个真管理员先看到的是一句假话。 */
    metaChecked: false,
    /* ---- 公开列表 ---- */
    items: [] as FeedbackCard[],
    total: 0,
    counts: { ...EMPTY_COUNTS } as FeedbackCounts,
    loading: false,
    /** 正在取**下一页**（不是第一页）。和 `loading` 分开是有用的：第一页要骨架、
     *  下一页要在列表尾巴上转个圈，**而且两者互不代表对方在跑** —— 「加载更多」
     *  的请求还在飞的时候按一下搜索，屏幕不该先整片闪成骨架再换内容。 */
    loadingMore: false,
    /** 「下一页从哪儿开始」= **已经向服务端取回过的行数**，不是 `items.length`。
     *
     *  没人插队时两者相等 —— 而这正是它以前写成 `items.length` 的原因。但 `items`
     *  是 `appendPage` 按 id 去重之后的数组：中间有人新提了一条反馈，下一次
     *  `pageStart` 就小于服务端眼里「我们已经翻到哪儿了」，请求回的全是手上已有的行，
     *  去重之后长度一动不动，于是「还有没有下一页」也永远不动 —— 屏幕上是一颗点得
     *  下去、点了没反应的按钮，外加一条被顶到偏移量之前、永远取不回来的新反馈。
     *  这个计数器只加不减，和服务端那个偏移量才是一回事。 */
    listRequested: 0,
    /** 命中缓存之后那次**背后的重拉**在不在飞。缓存命中时 `loading` 故意留 false
     *  （缓存先画，别闪骨架），于是 `loadMoreList` 那道 `this.loading` 门在这段窗口
     *  里是开着的：它按**旧的长列表**算偏移量，而这次重拉马上会把 `items` 换成短的
     *  第一页，两个响应接起来就漏掉中间一整段，并且再也补不上。 */
    listRefreshing: false,
    query: '',
    tab: 'all' as FeedbackTab,
    /* ---- 公开列表的四个筛选（反馈中心那一排控件）。
       它们是**服务端**的筛选（`GET /feedback?author=&status=&kind=&since=`），和栏位
       叠在一起：栏位说「哪一栏」，这四个说「那一栏里哪些」。前端再筛一遍就是第二份
       实现 —— 而两份实现漂开的表现是「翻页之后筛选悄悄失效」，页面上看不出异常。 ---- */
    filterAuthor: '',
    filterKind: null as FeedbackKind | null,
    filterStatus: null as FeedbackStatus | null,
    /** 时间窗口，按「最近 N 天」存。`null` = 不限。 */
    filterDays: null as number | null,
    /* ---- 管理端列表 ---- */
    adminItems: [] as FeedbackCard[],
    adminTotal: 0,
    adminLoading: false,
    adminTab: 'public' as AdminTab,
    /** 管理端这一页的问法：栏位 / 搜索词 / 排序 / 起点，加上看板点进来时带的三段
     *  日期窗口。改其中任何一样都是**换了一个问题**，所以它们住在一起，而且每个
     *  setter 都自己负责把起点归零 —— 各自为政时，「换了栏位但页码没归零」这类
     *  组合是必然出现的。 */
    adminQuery: '',
    adminSort: 'new' as AdminSort,
    adminPageStart: 0,
    /** 看板上「7 日新增 / 7 日解决 / 7 日上线」三个数字点进来时带着的那一段。
     *  值是链接上的那一天，原样送去查询 —— 「七天前是几号」由看板算一次，这里
     *  再算一次就会差一个时区。 */
    adminSince: null as string | null,
    adminResolvedSince: null as string | null,
    adminDeployedSince: null as string | null,
    /* ---- 看板的汇总。**一个分类一份**（`/admin/stats/{feedback,usage,platform,…}`），
       因为服务端一分类一块：切到哪一类才拉哪一类，各自留着自己那份（切回来不再拉一次，
       也不会出现「切到用量却画着反馈的数」）。和上面那份列表是**两份数据** —— 列表回
       的是「这一栏的第一页」，这里是窗口内的聚合；拿列表在前端数一个聚合出来，就是把
       筛选和分页各抄第二份，数出来的数字迟早和旁边那一栏对不上。 ---- */
    stats: {
      feedback: null,
      usage: null,
      platform: null,
      performance: null,
      pipeline: null,
      product: null,
      integrations: null,
    } as { [K in StatsKind]: StatsShapes[K] | null },
    /** 看板当前停在哪一类。页面上的分类控件读它、也写它 —— 分类是**这一页的**状态，
       但它决定了下一个请求打哪条接口，所以由 store 记着，页面重挂载时不会跳回第一类。 */
    statsKind: 'feedback' as StatsKind,
    /** 看板**每一类各自**的加载中。**不能是一把全局的布尔**：那个标志会被任何一类的
     *  响应在 `finally` 里清掉（判据只比它自己那一类的代次），于是「前一类的请求还在飞、
     *  后一类先回来了」这一瞬间，当前这一类的骨架会提前收掉、数字画成「—」、折线图落进
     *  「暂无数据」—— 屏幕上说这一类没有数据，而它正在路上。更糟的一种是：先回来那一趟
     *  若是**失败**的，它那句 `error` 配上「当前这一类还没拿到」，整页会翻成「看板加载
     *  失败」，哪怕当前这一类马上就会成功。
     *  对外仍然只暴露一个 `statsLoading`（下面那个 getter，读的是**当前这一类**那一格），
     *  所以页面上的读法一行都不用改。 */
    statsBusy: {
      feedback: false,
      usage: false,
      platform: false,
      performance: false,
      pipeline: false,
      product: false,
      integrations: false,
    } as Record<StatsKind, boolean>,
    /* ---- 我的反馈（`/feedback/mine`）。和上面那份公开列表是**两套数据**，
       不是同一份的两个视图：公开列表按栏位筛全平台，这一份按「和我的关系」筛，
       服务端的 WHERE 就不是同一个。 ---- */
    mineItems: [] as FeedbackCard[],
    mineTotal: 0,
    mineLoading: false,
    /** 同 `loadingMore`，只是「我的反馈」那一份。 */
    mineLoadingMore: false,
    /** 同 `listRequested`，只是「我的反馈」那一份。 */
    mineRequested: 0,
    /** 同 `listRefreshing`，只是「我的反馈」那一份。 */
    mineRefreshing: false,
    /* ---- 详情。`detail` 是**当前这一条**；换一条时整个对象换掉。 ---- */
    detail: null as FeedbackDetail | null,
    detailLoading: false,
    /** 详情页正在看哪一条。用来判断「这次回来的详情是不是我要的那条」—— 慢响应
     *  后到时，先到的那条已经渲染了，后到的会把页面换成另一条。 */
    detailId: null as string | null,
    /** 顶层评论正在取下一页。 */
    moreCommentsLoading: false,
    /** 哪几栋楼正在取楼内的下一页回复，按顶层评论 id。
     *  **这不只是给按钮画个转圈的**：同一个游标取两遍会把同一段回复追加两遍，屏幕上
     *  是两条一模一样的回复（`key` 还会撞）。所以真正的门在 action 里，这个状态是
     *  给按钮看的。 */
    moreRepliesLoading: {} as Record<string, boolean>,
    /* ---- 提交表单：`draft` 是那一份表单，三个入口共用（反馈中心、我的反馈、
       会话里的 agent 提案卡）。**「表单开着没有」不在 store 里** —— 提交表单有两个
       壳（独立页面 `/feedback/new`、会话里的对话框），壳自己知道自己在不在屏幕上
       （路由在不在、dialog 开不开）。store 里再留一个布尔就是同一个事实的第二份
       拷贝，而两份拷贝漂开的表现是「界面开着、按钮按不动」这种最难查的错位。 ---- */
    draft: emptyDraft(),
    /** 手上这份是不是从盘上**捞回来**的（而不是刚打出来的）。表单据此说一句话
     *  （「已恢复上次没写完的草稿」+ 丢弃），见 `openSubmit`。 */
    draftRestored: false,
    submitting: false,
    /** 上一次失败**原话**。页面直接显示它，不另写一句「操作失败」。 */
    error: null as string | null,
    /** 提交成功后给页面的一个一次性提示（v-snackbar 读它）。 */
    lastSubmittedId: null as string | null,
  }),

  getters: {
    /** 我是不是平台管理员。**服务端说了算**；meta 没到之前一律按「不是」。 */
    isAdmin(state): boolean {
      return !!state.meta?.is_admin
    },
    /** 状态梯子：服务端给的那份，没到之前用本地兜底（见 lib/feedbackMeta.ts）。 */
    statusLadder(state): FeedbackStatus[] {
      const fromServer = state.meta?.status_ladder
      return fromServer?.length ? fromServer : STATUS_LADDER
    },
    /** 「热门」的三个数：够多少分（`hotThreshold`）、一个支持几天打对折
     *  （`hotHalfLifeDays`）、不够分时至少补几条（`hotMinItems`）。
     *
     *  **前端不拿它们排序**：筛选和排序都在服务端，客户端手上的已经是排好的行，
     *  再算一遍屏幕上就有两套热度。它们在这里只有一个用处 —— 把这一栏的规则**说给
     *  人听**（反馈中心那一行小字）。一个「支持数」说不清「上周爆的和今天爆的同权」，
     *  半衰期这个数才说得清。
     *
     *  兜底值和服务端的常量一致（见 `repositories.py` 的 `HOT_*`）：meta 拿不到时
     *  那一行照样说得通，不会显示一个空白。 */
    hotThreshold(state): number {
      return state.meta?.hot_score ?? 2
    },
    hotHalfLifeDays(state): number {
      return state.meta?.hot_half_life_days ?? 14
    },
    hotMinItems(state): number {
      return state.meta?.hot_min_items ?? 5
    },
    /** 后面还有没有。判据是**取回来的行数**和 `total`，不是 `items.length`：后者是
     *  去重之后的长度，有人插队时会永远小于 `total`，按钮就永远在（见
     *  `listRequested`）。仍然**用服务端给的 `total` 判**，不用「这一页是不是满的」：
     *  一页 50 条、总共正好 50 条时，「满员」会让人多按一次才看见空页。 */
    listHasMore(state): boolean {
      return state.listRequested < state.total
    },
    mineHasMore(state): boolean {
      return state.mineRequested < state.mineTotal
    },
    /** 当前停着的那一类在不在加载中。页面读的是它，所以「切到另一类」不会把已经拿到的
     *  这一类按成骨架。 */
    statsLoading(state): boolean {
      return state.statsBusy[state.statsKind]
    },
    tabCounts(state): Record<FeedbackTab, number> {
      return {
        all: state.counts.all,
        hot: state.counts.hot,
        active: state.counts.active,
        resolved: state.counts.resolved,
      }
    },
    /** 管理端的翻页。**边界由自己手上的那一页算，不靠服务端**：`adminTotal` 是
     *  整个栏位的条数，用它算「下一页还有没有」需要知道每页多大、而且翻到最后一
     *  页时会多按一次才发现是空的。手上这一页满员就还有下一页 —— 只有一条边界
     *  情况（最后一页正好满员），代价是那一次点下去会看到空页，而反过来（先按了
     *  才去问服务端）要多一次往返。 */
    adminHasPrev(state): boolean {
      return state.adminPageStart > 0
    },
    adminHasNext(state): boolean {
      return state.adminItems.length >= PAGE_SIZE
    },
  },

  actions: {
    /* ---- 词表 ---- */

    /** 拉词表和「我是不是管理员」。页面挂载时调一次；失败不致命 —— 颜色和标签
     *  本地都有一份，梯子也有兜底，所以这里不把错误抛给页面。 */
    async loadMeta(): Promise<void> {
      try {
        this.meta = await getFeedbackMeta()
      } catch {
        // 见上：词表拿不到不该让整页空白。
      } finally {
        // 失败也算「问过」：否则页面会永远停在「正在确认权限」，比看到一句
        // 「你的账号不在管理员名单里」更难查。
        this.metaChecked = true
      }
    },

    /* ---- 计数 ---- */

    /** `counts` 的**唯一**写入口 —— 全文件只有这一个地方写 `this.counts`。
     *
     *  为什么是一个动作而不是几处赋值：那几处各自只回答「我这份数字是何时问到的」，
     *  没有一处知道别处的事，于是「先发的后到」这种顺序问题只能靠每个调用点自己
     *  小心；漏一个的表现是屏幕上的未读数跳回去一次、下一轮刷新又自己好了 ——
     *  最难查的那一种。
     *
     *  `rev` 要在**发出请求那一刻**用 `takeCountsRev()` 领，不能等拿到响应再领：
     *  那时候领到的号说的是「我什么时候回来的」，正好是这里要挡的那件事。本地
     *  动作（推已读游标）没有请求可等，当场领一个 —— 它就是此刻最新的东西。 */
    _commitCounts(next: FeedbackCounts, rev: number): void {
      // 号相等时放行（`<` 而不是 `<=`）：同一个号只可能来自同一次领号。
      // 号大的赢，与到达顺序无关。
      if (rev < countsRev) return
      countsRev = rev
      this.counts = next
    },

    /* ---- 列表 ---- */

    /** 拉当前 Tab + 搜索词的那一页。
     *
     *  **命中缓存的定义是「问的还是同一个问题」**（Tab + 搜索词）。命中就先把手上
     *  那一页画出来（`loading` 保持 false，不闪骨架），然后**照样重拉一次**：缓存
     *  只是先画，不是答案 —— 这中间别人可能提了新的、管理员可能改了状态，而这一页
     *  上没有任何东西能知道。回来之后照常替换，有没有缓存走的是同一条路。
     */
    /** 公开列表这一趟问的**是哪个问题** —— 栏位 + 搜索词 + 四个筛选，一次算成
     *  `{key, params}`：`key` 是缓存指纹，`params` 是请求体。
     *
     *  **两样必须同源，而且只有这一处**。上一版把它们分头写在 `loadList` 里，于是
     *  `loadMoreList` 自己手写了一份只有 `{tab, q}` 的请求 —— 表现是「第 2 页起筛选
     *  悄悄失效，不带筛选的行拼在筛选结果下面」，而两份各自看着都对。指纹漏掉筛选的
     *  表现则是「换一个筛选、界面换了、列表还是上一份缓存」。
     */
    _listQuestion(): { key: string; params: FeedbackListQuery } {
      const q = this.query.trim()
      const author = this.filterAuthor.trim()
      // 「最近 N 天」在这里折成一个**时刻**发给服务端：窗口的对齐由服务端那套
      // （半开的 UTC 日）说了算，前端只负责说「从现在往回 N 天」。
      const since = this.filterDays === null ? null : new Date(Date.now() - this.filterDays * 86400_000).toISOString()
      return {
        key: [this.tab, q, author, this.filterKind, this.filterStatus, since].join('\u0000'),
        params: {
          tab: this.tab,
          q,
          pageSize: PAGE_SIZE,
          author,
          kind: this.filterKind,
          status: this.filterStatus,
          since,
        },
      }
    },

    async loadList(): Promise<void> {
      const seq = ++listSeq
      // 号要在**发请求之前**领，见 `_commitCounts`。
      const rev = takeCountsRev()
      const { key, params } = this._listQuestion()
      const cached = listCache?.key === key ? listCache : null
      if (cached) {
        this.items = cached.items
        this.total = cached.total
        // 缓存里那一份就是「上一次翻到哪儿」——偏移量接着它走，不接着这次还在飞的
        // 第一页走（`listRefreshing` 保证这两件事不会同时在跑）。
        this.listRequested = cached.items.length
        this.loading = false
        this.listRefreshing = true
      } else {
        this.loading = true
      }
      this.error = null
      try {
        const page = await listFeedback(params)
        // 只有最后一次请求的结果算数：防抖挡不住「先发的那次后到」。
        if (seq !== listSeq) return
        this.items = page.data
        this.total = page.total
        this.listRequested = page.data.length
        listCache = { key, items: page.data, total: page.total }
        this._commitCounts(page.counts, rev)
      } catch (error) {
        if (seq !== listSeq) return
        this.error = message(error, '反馈列表加载失败')
        // 失败回到「一条都没有 + 一句错」那一态，**和没有缓存时完全一样** ——
        // 页面只有那几种状态（加载中 / 有内容 / 一条都没有 / 拉失败），让旧内容
        // 和错误条同时挂着是第五种，没人设计过它，而「重试」那个按钮正好长在空
        // 态里：不清空就等于把它一起藏起来。
        this.items = []
        this.total = 0
        this.listRequested = 0
        listCache = null
      } finally {
        // 两个标志只有**最后那一次**请求收得回来：被顶掉的那次提前关掉的话，正在
        // 飞的那次重拉就没人替它关门了（`listRefreshing` 只在缓存命中那一路置位）。
        if (seq === listSeq) {
          this.loading = false
          this.listRefreshing = false
        }
      }
    },

    /** 切栏位。必然重新拉 —— 栏位是**服务端**的筛选，本地过滤是第二份实现。 */
    setTab(tab: FeedbackTab): void {
      if (this.tab === tab) return
      this.tab = tab
      void this.loadList()
    },

    /** 改一个筛选就重拉。**不防抖**：下拉是离散的一次选择，不像搜索那样每敲一个字
     *  都变；而这一下必然换一批数据，本地筛是第二份实现。 */
    setFilter(patch: {
      author?: string
      kind?: FeedbackKind | null
      status?: FeedbackStatus | null
      days?: number | null
    }): void {
      if (patch.author !== undefined) this.filterAuthor = patch.author
      if (patch.kind !== undefined) this.filterKind = patch.kind
      if (patch.status !== undefined) this.filterStatus = patch.status
      if (patch.days !== undefined) this.filterDays = patch.days
      void this.loadList()
    },

    /** 有筛选在生效没有。页面据此决定要不要画「清除筛选」那一颗。 */
    hasFilters(): boolean {
      return (
        this.filterAuthor.trim() !== '' ||
        this.filterKind !== null ||
        this.filterStatus !== null ||
        this.filterDays !== null
      )
    },

    /** 一次清干净：**栏位、搜索词、四个筛选**，然后拉一次。
     *
     *  **只发一次请求**，不是清完让每个控件各自触发一遍（那会发四个，而且中间三个
     *  页面都停在半筛状态）。栏位和搜索词也在内，是因为空态那颗「清除筛选」和筛选条
     *  那颗调的是同一个动作：被筛选筛空的人按它，期望的是回到「什么都没筛」的样子。
     */
    clearFilters(): void {
      this.filterAuthor = ''
      this.filterKind = null
      this.filterStatus = null
      this.filterDays = null
      this.tab = 'all'
      this.query = ''
      void this.loadList()
    },

    /** 改搜索词。防抖之后重新拉：搜索也在服务端，它搜的字段比前端能拿到的那几个多。 */
    setQuery(query: string): void {
      this.query = query
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = setTimeout(() => {
        searchTimer = null
        void this.loadList()
      }, SEARCH_DEBOUNCE_MS)
    },

    /** 只改搜索词、不发请求。用在「清除筛选」上：那个动作要立刻回到全量，
     *  而不是等 250ms（点了按钮却没动静，读起来像坏了）。 */
    async clearQuery(): Promise<void> {
      this.query = ''
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = null
      await this.loadList()
    },

    /** 取列表的下一页，接在手上这份后面。
     *
     *  **四道门都在这里，不在按钮上**：`disabled` 只挡得住鼠标，回车、快速双击、
     *  以及慢响应期间的那一下都会绕过去。四道各挡一个具体的坏结果 ——
     *  `loadingMore` 挡「同一段追加两遍」，`loading` 挡「第一页还在飞的时候偏移量
     *  按旧清单算、屏幕上出现一段谁也记不得的空档」，`listRefreshing` 挡同一件事的
     *  另一半（缓存命中时 `loading` 是故意不上锁的，见那个字段），`!listHasMore`
     *  挡「到底了还在取」。 */
    async loadMoreList(): Promise<void> {
      if (this.loadingMore || this.loading || this.listRefreshing) return
      if (!this.listHasMore) return
      // 这一页属于**发它时那个问题**（栏位 + 搜索词）。`seq` 是那一刻的号：栏位或
      // 搜索词在飞的这段时间里变了的话，回来的是上一个问题的第 N 页，接上去就是
      // 两份清单拼在一起 —— 所以它回来了也只配丢掉。
      const seq = listSeq
      const rev = takeCountsRev()
      // **问题只有一份**（见 `_listQuestion`）。上一版这一行是手写的 `{tab, q, …}`，
      // 于是第 2 页起**四个筛选整个丢掉**：筛选结果后面接着一堆不匹配的行，而两份
      // 各自看着都对。
      const { key, params } = this._listQuestion()
      // 偏移量是**取回来过的行数**，不是 `items.length`（见 `listRequested`）。
      const pageStart = this.listRequested
      this.loadingMore = true
      try {
        const page = await listFeedback({ ...params, pageStart })
        if (seq !== listSeq) return
        this.items = appendPage(this.items, page.data)
        this.total = page.total
        // 服务端这次真的交出了几行，偏移量就往前走几行 —— 去重丢掉的那几行也算
        // 「服务端已经给过了」，重复要它们才是这一页卡死的原因。
        this.listRequested += page.data.length
        // 缓存跟着长：翻进详情页再退回来时，用户已经展开的那几页还在，不必从头再
        // 点一遍。代价是下一次 `loadList` 命中它之后会重拉第一页、把长度收回 50 ——
        // 这是「缓存只是先画」那句话本来就承认的事（见 `loadList`）。
        listCache = { key, items: this.items, total: page.total }
        this._commitCounts(page.counts, rev)
      } catch (error) {
        if (seq !== listSeq) return
        // **不清空已经画出来的那些** —— 这一点和 `loadList` 的失败处理正好相反，
        // 因为失败的东西不一样：那里失败的是「整页的内容」，这里只是「再多一点」。
        // 翻页失败退回第一页，是拿「看不见任何东西」去换一个本来就没拿到的增量。
        // 两个页面都已经会画「列表非空时的失败」（error + 有内容 → 列表上方一条），
        // 所以这句话放出去有人接。
        this.error = message(error, '加载更多失败')
      } finally {
        // 无条件还门：这个标志说的是「这一次调用在飞」，和 `seq` 是不是还是自己
        // 无关。挂在 seq 上的话，一次被栏位切换作废的翻页会把门永远锁上。
        this.loadingMore = false
      }
    },

    /* ---- 我的反馈 ---- */

    /** 拉「和我有关的那一摞」：我提的 + 我替谁提的 + 指派给我的。三个来源合成一个
     *  清单是**服务端**的定义（`/feedback/mine`），前端不再分栏 —— 分栏要有「这条
     *  为什么在我的清单里」，而那个字段服务端不给；按 handle 在前端猜一遍等于把
     *  可见性规则抄第二份，抄错的那天就是有人看见不该看见的条目。
     *
     *  访客（没登录）拿到的也是这一条路：服务端回空列表而不是 401，所以这里和公开
     *  列表一样只是「没有」。 */
    async loadMine(): Promise<void> {
      const seq = ++mineSeq
      // 这一页没有参数，所以「问的还是同一个问题」永远成立 —— 有就拿去先画。
      // 它回答的是「我提的/指派给我的」，所以**身份一变就必须已经清掉**：
      // `resetFeedbackCaches()` 管这件事。
      const cached = mineCache
      if (cached) {
        this.mineItems = cached.items
        this.mineTotal = cached.total
        this.mineRequested = cached.items.length
        this.mineLoading = false
        this.mineRefreshing = true
      } else {
        this.mineLoading = true
      }
      this.error = null
      try {
        const page = await listMyFeedback({ pageSize: PAGE_SIZE })
        if (seq !== mineSeq) return
        this.mineItems = page.data
        this.mineTotal = page.total
        this.mineRequested = page.data.length
        mineCache = { items: page.data, total: page.total }
      } catch (error) {
        if (seq !== mineSeq) return
        this.error = message(error, '「我的反馈」加载失败')
        // 同 `loadList`：失败就是失败那一态，和没有缓存时一模一样 —— 页面上
        // 「拉失败」和「你还没有提过反馈」是两句分开的话，但都得是**清空之后**
        // 才画得出来。
        this.mineItems = []
        this.mineTotal = 0
        this.mineRequested = 0
        mineCache = null
      } finally {
        if (seq === mineSeq) {
          this.mineLoading = false
          this.mineRefreshing = false
        }
      }
    },

    /** 「我的反馈」的下一页。门和 `loadMoreList` 一样，少一道「问题变了」——
     *  这一页没有参数，它的问题永远是同一个（「和我有关的那些」），变的只有身份，
     *  而身份换了会走 `resetFeedbackCaches()` 那条路。 */
    async loadMoreMine(): Promise<void> {
      if (this.mineLoadingMore || this.mineLoading || this.mineRefreshing) return
      if (!this.mineHasMore) return
      const seq = mineSeq
      const pageStart = this.mineRequested
      this.mineLoadingMore = true
      try {
        const page = await listMyFeedback({ pageSize: PAGE_SIZE, pageStart })
        if (seq !== mineSeq) return
        this.mineItems = appendPage(this.mineItems, page.data)
        this.mineRequested += page.data.length
        this.mineTotal = page.total
        mineCache = { items: this.mineItems, total: page.total }
      } catch (error) {
        if (seq !== mineSeq) return
        // 同 `loadMoreList`：已经画出来的留着，失败只在列表上方说一句。
        this.error = message(error, '加载更多失败')
      } finally {
        this.mineLoadingMore = false
      }
    },

    /* ---- 详情 ---- */

    /** 拉一条的全部内容。看不见的 id 服务端回 404，这里如实把它记成「没有」。
     *
     *  命中缓存就**先画旧的那份、不置空**：置空的那一下整页会闪成骨架，而「打一
     *  条刚看过的反馈」恰恰是最常见的那次导航（列表点进去、返回、再点进去）。
     *  **背后照样重拉**，回来才替换 —— 和 `loadList` 一个口径：缓存只是先画。
     *
     *  它**不碰**管理端那条路，原因写在 `detailCache` 上。
     */
    async loadDetail(id: string): Promise<void> {
      this.detailId = id
      const cached = detailCache.get(id) ?? null
      this.detail = cached
      // 翻页的进度跟着这一条走：换一条反馈之后，上一条的「还剩几页」和「哪几栋楼
      // 正在取回复」都不成立了，留着只会让新页面上的按钮一进来就是转圈状态。
      // 这一句在缓存命中时也不能省：楼里的「正在取回复」挂的是**上一个 id**。
      this.moreCommentsLoading = false
      this.moreRepliesLoading = {}
      this.detailLoading = cached === null
      this.error = null
      try {
        const detail = await getFeedback(id)
        if (this.detailId !== id) return
        this.detail = detail
        detailCache.set(id, detail)
      } catch (error) {
        if (this.detailId !== id) return
        // 失败回到「这条反馈打不开」那一态（见 `loadList` 那段：页面只有那几种
        // 画法，旧内容和错误条同时挂着是第五种）。
        this.detail = null
        this.error = message(error, '这条反馈打不开')
        // 服务端说这条不该看见了（403/404）：缓存里那份现在是**错**的，留着它会在
        // 每一次进来时先画一条不该出现的内容。其余失败（网络抖了一下）**留着** —
        // 那一份仍然是这个人自己的、也仍然大概是对的，下次进来还能先画上。
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          detailCache.delete(id)
        }
      } finally {
        if (this.detailId === id) this.detailLoading = false
      }
    },

    /* ---- 支持 ---- */

    /** 支持 / 取消支持。**回的是写完之后的服务端计数**，不是本地 ±1：两个人同时
     *  点会各自渲染出一个从来没存在过的数字。 */
    async toggleSupport(id: string): Promise<void> {
      const card = this._find(id)
      if (!card) return
      try {
        const result = card.supported ? await unsupportFeedback(id) : await supportFeedback(id)
        this._patch(id, { supports: result.count, supported: result.supported })
        // 栏位上的数字也要跟着动：「热门」是服务端按「支持数 ≥ 门槛且未解决」现算的，
        // 就在这一下跨过门槛的条目，本地那份计数当场就对不上了（列表 6 条、Tab 上写着
        // 5）。计数只能问服务端，前端再算一遍就是第二份实现。
        void this.refreshCounts()
      } catch (error) {
        // 办完了的反馈会被拒（412）。那不是故障，是「别再点了」—— 如实显示服务端
        // 那句话，比一个静默失败好。
        this.error = message(error, '操作失败')
      }
    },

    /** 重新问一次计数。失败不报错：Tab 上的数字晚一拍更新，不值得打断人。
     *
     *  这条路上**只有计数**，没有一个列表可以顺手替它做顺序判断，所以它完全靠
     *  `_commitCounts` 那把号：晚发的不会被早发的盖掉。
     */
    async refreshCounts(): Promise<void> {
      const rev = takeCountsRev()
      try {
        const counts = await getFeedbackCounts()
        this._commitCounts(counts, rev)
      } catch {
        // 见上。
      }
    },

    /** 清掉上一次失败的原话。页面上的错误条靠它自己的关闭按钮调 —— 不这么做的话，
     *  一次失败会一直挂在页面上，直到下一个动作顺手把它覆盖。 */
    clearError(): void {
      this.error = null
    },

    /* ---- 评论 ---- */

    /** 发一条评论。`parentId` 指向**任意**一条评论：层级由服务端折上去，前端不判断。
     *  「这条在回谁」（`reply_to_handle`）也是服务端写下来的 —— 折到顶层之后前端再也
     *  推不出来，猜一个的结果是每个人看到的指代都不一样。
     *
     *  **返回成功与否**，调用方靠它决定「草稿留着还是清掉」：一次 500 之后把用户写了
     *  两段的话清掉，是这一版要修掉的东西之一。 */
    async addComment(id: string, body: string, parentId?: string): Promise<boolean> {
      const text = body.trim()
      if (!text) return false
      try {
        const created = await createFeedbackComment(id, text, parentId ?? null)
        const detail = this._detailIfCurrent(id)
        if (detail) {
          detail.thread = [...detail.thread, created]
          detail.comments += 1
          // 新回复要把它那一栋的**总数**也加一。那个数是「这栋楼一共几条回复」，
          // 客户端拿它和手上条数比较来决定「展开更多」是摊开还是去取下一页 ——
          // 少加这一个，刚发出去的回复会让这两个数对不上。
          //
          // 认的是**服务端折过的**目标（`created.parent_id`），不是这里的 `parentId`：
          // 回一条回复时，服务端会把它折到那栋楼的顶层，而 `parentId` 指的是一条
          // 回复（它自己没有楼，`reply_count` 恒为 0）。按 `parentId` 找就会加到
          // 一条没有楼的回复上，屏幕上那栋楼的总数永远差一条。
          if (created.parent_id) {
            const top = detail.thread.find((c) => c.id === created.parent_id)
            if (top) top.reply_count += 1
          }
        }
        return true
      } catch (error) {
        this.error = message(error, '评论发送失败')
        return false
      }
    },

    /** 往下翻**顶层评论**那一层。游标来自详情（`thread_next_cursor`），取完就没有了。
     *
     *  这里挡重复点击不是优化：同一个游标取两遍，同一个 `items` 会被追加两遍 ——
     *  屏幕上出现两条一模一样的评论，`:key` 也撞。 */
    async loadMoreComments(id: string): Promise<void> {
      const detail = this._detailIfCurrent(id)
      const cursor = detail?.thread_next_cursor
      if (!detail || !cursor || this.moreCommentsLoading) return
      this.moreCommentsLoading = true
      this.error = null
      try {
        const page = await listFeedbackComments(id, { after: cursor })
        const current = this._detailIfCurrent(id)
        if (!current) return
        current.thread = [...current.thread, ...page.items]
        current.thread_next_cursor = page.next_cursor
      } catch (error) {
        this.error = message(error, '评论加载失败')
      } finally {
        this.moreCommentsLoading = false
      }
    },

    /** 取**某一栋楼里**的下一段回复。游标挂在顶层评论那一行上（`replies_next_cursor`），
     *  和上面那条一样由服务端发、客户端原样送回去。 */
    async loadMoreReplies(id: string, parentId: string): Promise<void> {
      const detail = this._detailIfCurrent(id)
      const anchor = detail?.thread.find((c) => c.id === parentId)
      const cursor = anchor?.replies_next_cursor
      if (!detail || !anchor || !cursor || this.moreRepliesLoading[parentId]) return
      this.moreRepliesLoading = { ...this.moreRepliesLoading, [parentId]: true }
      this.error = null
      try {
        const page = await listFeedbackComments(id, { parentId, after: cursor })
        const current = this._detailIfCurrent(id)
        // 请求在飞的时候可能已经换了详情；换了就什么都不做（`_detailIfCurrent` 会
        // 挡住）。也可能这一栋楼整个被删了 —— 那 `kept` 里找不到锚点，同样收手。
        const kept = current?.thread.find((c) => c.id === parentId)
        if (!current || !kept) return
        current.thread = [...current.thread, ...page.items]
        kept.replies_next_cursor = page.next_cursor
      } catch (error) {
        this.error = message(error, '回复加载失败')
      } finally {
        const next = { ...this.moreRepliesLoading }
        delete next[parentId]
        this.moreRepliesLoading = next
      }
    },

    /** 点赞 / 取消点赞一条评论。**回的是写完之后的服务端计数**，不是本地 ±1 —— 和
     *  `toggleSupport` 同一句：两个人同时点会各自渲染出一个从来没存在过的数字。
     *
     *  这里**不**调 `refreshCounts()`，和 `toggleSupport` 有意不同：评论点赞不进任何
     *  一个 Tab 的计数，「热门」看的是反馈级的支持数。没头没脑地多问一次，只是一次白
     *  跑的请求。 */
    async toggleCommentLike(id: string, commentId: string): Promise<void> {
      const comment = this._commentOf(id, commentId)
      if (!comment) return
      try {
        const result = comment.liked
          ? await unlikeFeedbackComment(id, commentId)
          : await likeFeedbackComment(id, commentId)
        // 重新取一次而不是改上面那个引用：请求在飞的时候页面可能已经换了详情，也可能
        // 有人又点了一下。`_commentOf` 会挡住前一种（拿不到就什么都不做）。
        const current = this._commentOf(id, commentId)
        if (!current) return
        current.likes = result.count
        current.liked = result.liked
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /** 删一条评论。删得掉谁由服务端的 `can_delete` 说了算，按钮也是照它画的；这里
     *  不发第二遍请求去问，只把服务端已经答过的那件事照做。
     *
     *  **本地也要按服务端的规则把楼里的回复一起拿掉。** 服务端删顶层评论时会连带删掉
     *  它下面的回复（那是「一条回复挂在一个查不到的父亲下」的那个孤儿），前端少做这
     *  一步，屏幕上就正是那个形状：父亲没了、回复还挂着，再一次刷新它们又都不见了。
     *  这是本仓库接受的那种镜像（和 `lib/feedbackMeta.ts::isClosed` 同一个道理：前后端
     *  都要回答同一个问题时，两份实现里至少一份要写明它跟的是哪一条）。 */
    async deleteComment(id: string, commentId: string): Promise<void> {
      try {
        await deleteFeedbackComment(id, commentId)
        const detail = this._detailIfCurrent(id)
        if (!detail) return
        const gone = detail.thread.filter((c) => c.id === commentId || c.parent_id === commentId)
        const kept = detail.thread.filter((c) => c.id !== commentId && c.parent_id !== commentId)
        detail.comments -= gone.length
        // 被删掉的是楼里的回复，那一栋的总数也要减。这只对**手上已经有的**那几条
        // 生效：回复分页之后，删掉一条还没取回来的回复（理论上做不到，屏幕上没有
        // 就没有按钮）这里数不到，那一栋的总数会偏高一条。偏高只会让「加载更多
        // 回复」多一次必然取到空页的点击，然后游标到底、按钮消失，不会留下一个
        // 少显示的回复。
        for (const row of gone) {
          if (!row.parent_id) continue
          const top = kept.find((c) => c.id === row.parent_id)
          if (top) top.reply_count = Math.max(0, top.reply_count - 1)
        }
        detail.thread = kept
      } catch (error) {
        this.error = message(error, '删除失败')
      }
    },

    /** 删掉**整条反馈** —— 作者删自己的，平台管理员删任何一条。
     *
     *  能不能删不在这里判：按钮出不出现看服务端回的 `can_delete`（和路由上那一处
     *  `may_delete_feedback` 是同一个判据）。这里只负责删完把自己手上那几份数据一起
     *  收干净 —— **少收一处，下一页就会画出一条点不开的反馈**，而那比不删更糟。
     */
    async deleteFeedback(id: string): Promise<boolean> {
      this.error = null
      try {
        await deleteFeedback(id)
      } catch (error) {
        this.error = message(error, '删除失败')
        return false
      }
      // 三份列表都在内：`adminItems` 是管理端那条路（管理员从队列点进来删的，删完
      // 回队列时那一条不该还在）。
      this.items = this.items.filter((row) => row.id !== id)
      this.mineItems = this.mineItems.filter((row) => row.id !== id)
      this.adminItems = this.adminItems.filter((row) => row.id !== id)
      detailCache.delete(id)
      if (this.detailId === id) {
        this.detailId = null
        this.detail = null
      }
      // 计数里它还占着一格。重问一次 —— 数字由服务端数，不在前端减一。
      void this.refreshCounts()
      return true
    },

    /* ---- 提交表单 ---- */

    /** **准备一份草稿**，不做「打开」这件事 —— 三个入口都先调它，然后把壳拉起来
     *  （两个列表入口 push 到 `/feedback/new`，会话里那张提案卡开对话框）。
     *
     *  `preset` 只有提案卡那条路会传：它整份替换表单内容，所以人自己打了一半的那份
     *  先挪去 parked。 */
    openSubmit(preset: Partial<FeedbackDraft> = {}): void {
      const incoming = Object.keys(preset).length > 0
      if (incoming) {
        // 人已经打了一半的那份挪去 parked、而不是丢掉：这一步整份替换表单内容，
        // 而替换不是他的本意（他点的是会话里那张卡）。下一次裸开表单时它会自己回来。
        parkFeedbackDraft(this.draft)
        this.draft = { ...emptyDraft(), ...preset }
        // 卡片带过来的那份是**新**的一份，不是从盘上捞的：那条「已恢复草稿」的提示
        // 跟着它一起清掉，否则刚从卡上进来的人会看到一句「已恢复上次没写完的草稿」，
        // 而屏幕上这份明明是芝士刚递过来的。
        this.draftRestored = false
        // 卡片带过来的那份也是「打了一半的东西」—— 关掉、刷新、再打开，它该还在。
        saveFeedbackDraft(this.draft)
      } else if (isDraftMeaningful(this.draft)) {
        // 手上这份就是最新的（`touchDraft` 一直在往盘上写），接着写。
      } else {
        // 手上是空的：先把上次被替换下去的那份拿回来，没有再取「上次没写完的」。
        // 这一步是给**页面自己刷新**（发版时 service worker 会 reload，见 pwa.ts）
        // 和「关掉又想起来」用的 —— 少这一步，几百字在用户眼皮底下消失。
        const restored = takeParkedFeedbackDraft() ?? loadFeedbackDraft()
        this.draft = { ...emptyDraft(), ...(restored ?? {}) }
        // 捞回来这件事**要在界面上说出来**（见 SubmitFeedbackForm 的那条提示）：
        // 打开表单看到一段不是自己刚打的字，不说一声就像串了别人的内容。
        this.draftRestored = isDraftMeaningful(restored)
      }
      // 上一次那条失败的原话是给**上一次**那个表单看的。新开一份时清掉，否则刚进
      // 来的人会看到一句「提交失败」，而他什么都还没提交。
      this.error = null
    },

    /** 表单里改了任何一栏，调用方在字段的 `update:model-value` 上打一下这里。
     *
     *  **落盘要防抖**：每敲一个字写一次 `localStorage` 是不必要的（而且中文输入法
     *  在候选阶段就会发事件）。防抖窗口是 `DRAFT_SAVE_DEBOUNCE_MS`，关表单和提交
     *  各会额外收一次尾（见 `closeSubmit` / `submit`），所以窗口里那几下不会漏。
     *
     *  **不传内容进来**：读的是 `this.draft` 那一刻的值。让调用方把值传进来就等于
     *  每个字段自己折一份，而它们迟早会漏掉一栏。 */
    touchDraft(): void {
      if (draftTimer) clearTimeout(draftTimer)
      draftTimer = setTimeout(() => {
        draftTimer = null
        saveFeedbackDraft(this.draft)
      }, DRAFT_SAVE_DEBOUNCE_MS)
    },

    /** 关掉表单不是「不要了」：把防抖窗口里那几下收尾写下去。**不负责关界面** ——
     *  壳自己关（页面 push 走、对话框合上），这里只管草稿和那一次性的提示。 */
    closeSubmit(): void {
      if (draftTimer) clearTimeout(draftTimer)
      draftTimer = null
      saveFeedbackDraft(this.draft)
      this.lastSubmittedId = null
    },

    /** 「丢弃草稿」：盘上**两份槽位**一起抹掉，表单回到空白。
     *
     *  两份都要抹是有意的：只清「正在写的」那一格，人会看到自己刚说不要的那份**下次
     *  打开又一字不差地回来**（它从 parked 那一格回来了），而「丢弃」这个词承诺的正是
     *  不回来。 */
    discardDraft(): void {
      if (draftTimer) clearTimeout(draftTimer)
      draftTimer = null
      forgetFeedbackDraft()
      this.draft = emptyDraft()
      this.draftRestored = false
      this.error = null
    },

    /** 提交。返回新条目的 **uuid**（路由用它），失败回 null 并把原因写进 error。
     *
     *  两条路都从这里走：普通提交是 `POST /feedback`；从提案卡来的那条是
     *  `POST /topics/{id}/feedback-proposals/{block_id}/accept` —— 后者的作者是提案
     *  的 agent、提交者是按下发送的人，两个都由服务端从卡和会话里取，所以客户端
     *  连作者名都不用传（传了也不算数）。
     *
     *  **提交完不关任何界面**：谁把表单摆在屏幕上，谁负责收它（页面 replace 到详情
     *  页，对话框自己合上）。store 在这里关界面的话，两个壳会各关一次，而第二次关
     *  的是别人。 */
    async submit(): Promise<string | null> {
      const draft = this.draft
      // 必填两栏都查：按钮 disabled 不是替代品 —— 这个 action 还有别的调用点（会话里
      // 那张卡、以后的快捷键），少查一栏就会出现一条只有标题、没有任何正文的反馈。
      // 提交的门槛是「标题 + 说明」，`repro` / `expectation` 保持选填，理由见
      // SubmitFeedbackForm 的注释。
      if (!draft.title.trim() || !draft.body.trim() || this.submitting) return null
      this.submitting = true
      this.error = null
      try {
        const body = toCreateBody(draft)
        const detail = draft.proposal
          ? await acceptFeedbackProposal(draft.proposal.topicId, draft.proposal.blockId, body)
          : await createFeedback(body)
        this.lastSubmittedId = detail.id
        this.draft = emptyDraft()
        this.draftRestored = false
        // 这份东西已经变成一条反馈了，盘上那份不该再留着：否则下次打开表单它整份
        // 弹回来，看着像「刚提交的那条又回到草稿里了」。**只清「正在写的」那一格** ——
        // 被替换下去的那份（有人自己打了一半的）和这次提交无关，还得在。
        if (draftTimer) clearTimeout(draftTimer)
        draftTimer = null
        clearLiveFeedbackDraft()
        // 列表和各种计数都变了：清掉，让下次挂载重新拉，而不是在这里手改数组
        // （手改的那份迟早和下一页对不上）。`mineItems` 也得清 —— 刚提的这条
        // 属于「我提的」，留着旧的会让「我的反馈」少一条，而那正是这一页要回答的
        // 那个问题。
        //
        // **缓存要一起丢**：只清屏幕上那份的话，下次挂载会命中缓存、把提交之前那
        // 一页原样画回来，再在背后换成新的 —— 那一下看上去就是「我刚提的反馈不见
        // 了」，而它其实只是还没被那次重拉带上。
        dropListCaches()
        this.items = []
        this.mineItems = []
        // 偏移量跟着数组一起归零：两者说的是同一件事（手上这份从哪儿来），
        // 只清一个的话下一次「加载更多」会从旧偏移量上接着要。
        this.listRequested = 0
        this.mineRequested = 0
        // 顺手把刚落地的这条写进详情缓存：提交之后紧接着去的就是它的详情页，而手上
        // 这一份就是服务端刚回的**整条**。这里能这么写不是猜的 —— `POST /feedback`
        // 和 `GET /feedback/{id}` 在服务端走的是同一个 `_detail(...)` 序列化，形状
        // 一样（见 routes/feedback.py）。
        detailCache.set(detail.id, detail)
        return detail.id
      } catch (error) {
        this.error = message(error, '提交失败')
        // 失败时**盘上那份留着**，而且这里补一次收尾：提交是防抖窗口里最可能发生的
        // 「人停下来了」，而那一下不该让人在刷新后丢掉刚写完的东西。
        if (draftTimer) clearTimeout(draftTimer)
        draftTimer = null
        saveFeedbackDraft(this.draft)
        return null
      } finally {
        this.submitting = false
      }
    },

    /* ---- 管理端 ---- */

    async loadAdmin(): Promise<void> {
      const seq = ++adminSeq
      // 这一页也带着一份计数（栏位上的数），和公开列表那份是**同一份**。所以它
      // 也要在发请求前领号 —— 不领的话它就绕过了 `_commitCounts` 那道门。
      const rev = takeCountsRev()
      this.adminLoading = true
      this.error = null
      try {
        const page = await listAdminFeedback({
          tab: this.adminTab,
          q: this.adminQuery.trim(),
          sort: this.adminSort,
          // `null`（没有这个窗口）折成 `undefined`：查询串里「没给」和「给了个空值」
          // 是两件事，前者才是「这段日子不筛」。
          since: this.adminSince ?? undefined,
          resolvedSince: this.adminResolvedSince ?? undefined,
          deployedSince: this.adminDeployedSince ?? undefined,
          pageStart: this.adminPageStart,
          pageSize: PAGE_SIZE,
        })
        if (seq !== adminSeq) return
        this.adminItems = page.data
        this.adminTotal = page.total
        this._commitCounts(page.counts, rev)
      } catch (error) {
        if (seq !== adminSeq) return
        this.error = message(error, '管理队列加载失败')
        this.adminItems = []
      } finally {
        if (seq === adminSeq) this.adminLoading = false
      }
    },

    setAdminTab(tab: AdminTab): void {
      if (this.adminTab === tab) return
      this.adminTab = tab
      // 换栏位回到第一页：第 3 页的第 4 条在另一个栏位里没有意义，留着页码会
      // 直接落到一个空页上 —— 看着像「这一栏一条都没有」。
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    setAdminSort(sort: AdminSort): void {
      if (this.adminSort === sort) return
      this.adminSort = sort
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    /** 三个日期窗口：看板上「7 日新增 / 7 日解决 / 7 日上线」点进来时带着的那一段。
     *  和栏位、排序同一条规矩 —— 换一个窗口就是换了一个问题，页码必须归零，否则
     *  第 3 页落在另一段时间里，看着像「这段时间一条都没有」。
     *
     *  三个分开而不是合成一个 action：链接上从来只带其中一个，而「都没变」时这三
     *  个都直接返回、连请求都不发 —— 合成一个之后，从别的页切回队列时的「清空窗口」
     *  那一次会白拉一页。 */
    setAdminSince(since: string | null): void {
      if (this.adminSince === since) return
      this.adminSince = since
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    setAdminResolvedSince(since: string | null): void {
      if (this.adminResolvedSince === since) return
      this.adminResolvedSince = since
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    setAdminDeployedSince(since: string | null): void {
      if (this.adminDeployedSince === since) return
      this.adminDeployedSince = since
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    /** 搜索框。**防抖在 action 里，不在页面上** —— 页面拿到的还是「用户打进来的
     *  每一个字」，由这里决定什么时候真去打服务端。
     *
     *  过期的响应必须扔：手速快时 `cai` 和 `caiy` 两个请求会同时在飞，先发的
     *  那次**可能后到**，把结果换成上一个词的那一页，而输入框里是新的词。所以
     *  每次新的输入都把序号推一格，回来晚的看到序号变了就直接丢。 */
    setAdminQuery(q: string): void {
      this.adminQuery = q
      this.adminPageStart = 0
      if (adminQueryTimer !== null) clearTimeout(adminQueryTimer)
      adminQueryTimer = setTimeout(() => {
        adminQueryTimer = null
        void this.loadAdmin()
      }, SEARCH_DEBOUNCE_MS)
    },

    clearAdminQuery(): void {
      if (adminQueryTimer !== null) {
        clearTimeout(adminQueryTimer)
        adminQueryTimer = null
      }
      if (!this.adminQuery) return
      this.adminQuery = ''
      this.adminPageStart = 0
      void this.loadAdmin()
    },

    adminPrev(): void {
      if (!this.adminHasPrev) return
      this.adminPageStart = Math.max(0, this.adminPageStart - PAGE_SIZE)
      void this.loadAdmin()
    },

    adminNext(): void {
      if (!this.adminHasNext) return
      this.adminPageStart += PAGE_SIZE
      void this.loadAdmin()
    },

    /** 光标挪到 `id` 上了：详情区手上那一份当场作废。
     *
     *  和 `loadAdminDetail` 开头那三行是同一件事，区别只在**什么时候**做。那个要等
     *  队列页那 150ms 防抖到期，而连按 `j`/`k` 时那支计时器每次都被重置 —— 于是整段
     *  连按期间详情区挂的一直是**上一条**的正文，左侧选中的行、头上的编号却已经是
     *  新的（F-06 说的就是这个）。作废必须发生在挪光标的那一刻。
     *
     *  `detailId` 一并前移不是顺手，是必须的：它还兼着「响应回来时这一条还算不算数」
     *  的判据（见下面 `loadAdminDetail`）。只清 `detail` 而不改它，那条还在路上的
     *  上一条的响应回来时会被当成这一条收下 —— 刚清掉的旧正文又贴回来了。
     *
     *  `id` 为 null（列表被筛空、光标没了）：作废，而且不该停在「正在拉」上。 */
    invalidateDetail(id: string | null): void {
      // 代次 +1：**已经在路上**的那些管理端详情响应从这一刻起全部作废。作废必须
      // 发生在这里而不是等页面那 150ms 防抖到期 —— 防抖只把「同一段连按」并成一次
      // 请求，它挡不住「光标先动了、请求还没发」那段时间里回来的一份旧响应：那期间
      // `detailId` 还是旧的，只按 id 判会把上一条的正文贴回来（F-06）。
      adminDetailSeq += 1
      this.detailId = id
      this.detail = null
      this.detailLoading = id !== null
      this.error = null
    },

    /** 管理端点开一条。详情走**管理端**那个端点（`/admin/feedback/{id}`）：它和公开
     *  那个回的字段一样，但前者的可见性判据是「管理员」，后者是「并集」。
     *
     *  **它不用 `detailCache`**，两件事都刻意：这一份可能是同一台机器上**另一个人**
     *  的会话留下来的，共享那份缓存等于让下一个读者先画出一份他本来就看不见的内容；
     *  而且管理端的响应带的字段集不一样，混进同一个 id 的槽里，公开那一页会先画一个
     *  形状不对的对象。缓存是给「同一个人反复开同一条公开详情」用的。 */
    async loadAdminDetail(id: string): Promise<void> {
      // 作废那一步只有一处写法，避免「清了 detail 忘了推 detailId」这种半截状态。
      this.invalidateDetail(id)
      // 取号**必须在 `invalidateDetail` 之后**：那一下自己就把代次 +1 了。这一份响应
      // 只有在「从这一刻起到它落地为止，没有人作废过、也没有人写过这条」时才作数 ——
      // 后者是它和 `detailId` 那条判据的分工：读和写落在**同一个 id** 上，id 一样
      // 判不出先后，只有代次能（见 `_adminWrite`）。
      const seq = adminDetailSeq
      try {
        const detail = await getAdminFeedbackDetail(id)
        if (seq !== adminDetailSeq || this.detailId !== id) return
        this.detail = detail
      } catch (error) {
        if (seq !== adminDetailSeq || this.detailId !== id) return
        this.error = message(error, '这条反馈打不开')
      } finally {
        // 被作废时**不复位** `detailLoading`：那面旗现在归更晚的那一次（新的读，或者
        // 刚写完那一下），这里复位等于替它宣布「不拉了」。
        if (seq === adminDetailSeq && this.detailId === id) this.detailLoading = false
      }
    },

    /** 人**按下**的那个已读（`M` 键、页头上那个按钮）：把未读游标推到此刻。
     *
     *  和下面那个自动的分开，而不只是「都推一次游标」：这一个**每次都要真发请求**。
     *  页面自己触发的那个带一个 id 去重集合（同一条只推一次），拿它当人手那一下的
     *  实现，就会出现「按了没反应」—— 打开一条超过 800ms 之后再按 `M`，那一按会被
     *  集合直接早退掉：未读数不减、徽标不动、也没有任何提示。手动那一下是幂等的
     *  （服务端推的就是同一个游标），但它不该是静默的。 */
    async markRead(): Promise<void> {
      try {
        await markFeedbackRead()
        // 这一下把游标推过了**此刻还开着的这一条**，所以页面那个自动已读再为它发一次
        // 请求就没有意义了 —— 记进集合里，让它 800ms 后那次自己跳过。
        // **只在成功之后记**：失败时不能记，否则自动那一条路也不会再试了。
        if (this.detailId) markedReadIds.add(this.detailId)
        // 当场领号：这一份是**此刻**最新的东西，比任何还在路上的响应都新 ——
        // 早一步发出的那次 loadList 回来时不该把未读数再点回去。
        this._commitCounts({ ...this.counts, unread: 0 }, takeCountsRev())
      } catch {
        // 游标推不动不值得报错 —— 下次打开还会再试一次。
      }
    },

    /** 进详情时**自动**走的那个已读：同一个 id 只真发一次请求。
     *
     *  和上面那个分开，因为触发的人不同：`markRead` 是人按下 M 键或按钮，按两次
     *  就是两次表态；这一条是页面自己触发的，而一条详情在一段时间里会被开开关关
     *  好几次（抽屉、返回列表再点开），每开一次推一次游标，等于把同一件事重复说了
     *  好几遍。
     *
     *  失败时把 id 放回去：游标没推成，这一条在服务端仍然是未读，记成「已标记」
     *  会让它再也清不掉 —— 那正是这一轮要修的那个死档。 */
    async markReadOnce(id: string): Promise<void> {
      if (markedReadIds.has(id)) return
      markedReadIds.add(id)
      try {
        await markFeedbackRead()
      } catch {
        markedReadIds.delete(id)
        return
      }
      // 先把徽标归零，再问一次服务端：`markFeedbackRead()` 推的是**整个人**的游标
      // （一直推到此刻），所以读过这一条之后没有一条还是未读的。本地减一是在前端
      // 算一遍服务端的账，而那张账本（谁在什么时候动过我的条目）前端看不见。
      // 当场领号（同 `markRead`）：这是一次本地动作，没有请求可等。
      this._commitCounts({ ...this.counts, unread: 0 }, takeCountsRev())
      void this.refreshCounts()
    },

    /** 推一个状态。服务端把状态和它那条时间线写在同一个事务里，所以这里回的是
     *  **刷新之后的整条详情**，不是「我把状态改成了什么」的本地推断。 */
    async setStatus(id: string, status: FeedbackStatus): Promise<void> {
      await this._adminWrite(id, () => setAdminFeedbackStatus(id, status))
    },

    async setPriority(id: string, priority: FeedbackPriority): Promise<void> {
      await this._adminWrite(id, () => patchAdminFeedback(id, { priority } satisfies FeedbackAdminPatch))
    },

    /** 指派 / 取消指派。空串是「清掉」—— 服务端把 `''` 和 `null` 分开读，
     *  后者是「这次别动它」。 */
    async assign(id: string, handle: string | null): Promise<void> {
      await this._adminWrite(id, () =>
        patchAdminFeedback(id, { assignee_handle: handle ?? '' } satisfies FeedbackAdminPatch)
      )
    },

    /** 标 / 取消标「安全问题」。标上之后这条对同事就不见了 —— 所以服务端顺手写一条
     *  时间线，这里回的是刷新后的详情。 */
    async setSecurity(id: string, security: boolean): Promise<void> {
      await this._adminWrite(id, () => patchAdminFeedback(id, { security } satisfies FeedbackAdminPatch))
    },

    /** 加一条内部备注。**只增不改**：回的是整条详情，`notes` 里就有新的那条。 */
    async addNote(id: string, body: string): Promise<void> {
      const text = body.trim()
      if (!text) return
      await this._adminWrite(id, () => createAdminFeedbackNote(id, text))
    },

    /** 管理端写操作共用的外壳：一次请求、一次刷新、一处错误处理。
     *
     *  `id` 是**这次写的是哪一条**，两个地方要用它：回填详情时对一下「抽屉里现在还
     *  是它吗」，以及列表刷新后的口径。 */
    async _adminWrite(id: string, call: () => Promise<FeedbackDetail>): Promise<void> {
      this.error = null
      try {
        const detail = await call()
        // 只在**还是这一条**时回填：写请求在飞的时候人可能已经点开了另一条，慢响应
        // 回来会把 `detailId` 拉回旧的那条 —— 新条目的抽屉要么显示「这条反馈打不开」，
        // 要么因为 `detailLoading` 永远不再复位而卡在「加载中…」。
        // 换掉 `state.detail` 本身，**不写回 `detailCache`**（这一份来自管理端端点，
        // 见 `detailCache` 那段）。缓存里那份留着旧值，下一次 `loadDetail` 会先画它、
        // 再被重拉覆盖 —— 代价是一次往返，换来的是「缓存里只存公开端点回的东西」
        // 这条不变量，它比省掉的那一次往返值钱。
        if (this.detailId === id) {
          this.detail = detail
          this.detailLoading = false
          // 代次 +1：**写之前发出去、写之后才回来**的那次读从此作废。没有这一下，
          // 一份比这次写更旧的快照会贴回来盖掉它，而屏幕上看着就是「这一按没生效」——
          // 管理员再按一次，于是状态白走一格。读和写落在同一个 id 上，只按 id 判
          // 分不出谁新谁旧，这条代次是唯一能判的那个（e2e 里真的踩到过：
          // 面板显示的还是「已收录」，而库里已经写成了「处理中」）。
          adminDetailSeq += 1
        }
        // 那一行也变了，而且可能在**别的一栏**里（改了安全问题就从公开栏挪进安全栏），
        // 所以整页重新拉一次。以前这里只是把数组清空、指望「下次挂载重拉」，可管理端
        // 这一页在抽屉关掉时既不重新挂载也不重新拉取 —— 于是表格画出来的是「这一栏
        // 没有反馈」这个假状态，不切栏位、不刷新页面就回不来。
        await this.loadAdmin()
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /* ---- 看板 ---- */

    /** 拉看板的**一个分类**。`kind` 缺省是当前停着的那一类，`days` 由页面给（默认 7，
     *  就是页头上那句「过去 7 天」）—— 窗口是页面的问题，不是 store 的：将来多一个
     *  「过去 30 天」就是换个参数。
     *
     *  **切分类要把当时那一类钉住**：请求发出去之后人才切的分类，回来的那一份是给上一个
     *  分类的，写进 `stats[kind]` 才对；写进「现在这一类」就成了「切到用量、画出来的是
     *  反馈的数」。所以 `kind` 在这里被捕获，不读 `this.statsKind`。
     *
     *  和列表一样要扔掉过期响应：R 键连按两次会发两个请求，而先发的那次可能后到。 */
    async loadStats(kind?: StatsKind, days = 7): Promise<void> {
      // `kind` 缺省是「当前停着的那一类」，但**在这里定下来**（不写进参数默认值：
      // 参数默认值里的 `this` 在 options store 里没有类型，而它读的正是 this）。
      const wanted: StatsKind = kind ?? this.statsKind
      const seq = ++statsSeq[wanted]
      this.statsBusy[wanted] = true
      this.error = null
      try {
        const stats = await getStats(wanted, { days })
        if (seq !== statsSeq[wanted]) return
        assignStats(this.stats, wanted, stats)
      } catch (error) {
        if (seq !== statsSeq[wanted]) return
        this.error = message(error, '看板加载失败')
      } finally {
        if (seq === statsSeq[wanted]) this.statsBusy[wanted] = false
      }
    },

    /* ---- 提案卡 ---- */

    /** 这个话题里还活着的提案卡。**已经「不用」过的不会回来** —— 那件事记在服务端
     *  （指纹），不是组件状态（原型那份一刷新就回来）。 */
    async loadProposals(topicId: string): Promise<FeedbackProposal[]> {
      try {
        return await listFeedbackProposals(topicId)
      } catch {
        // 提案是「顺路问一句」，拉不到就当没有 —— 不能因为它让对话栏报错。
        return []
      }
    },

    /** 「不用」。落一行，之后同指纹的卡不会再出现。 */
    async dismissProposal(topicId: string, blockId: string): Promise<void> {
      try {
        await dismissFeedbackProposal(topicId, blockId)
      } catch (error) {
        this.error = message(error, '操作失败')
      }
    },

    /* ---- 内部小工具（下划线开头：不是给页面用的 API） ---- */

    /** 一条反馈现在在**哪一份数据**里。三份列表都要找。前两份（公开、管理）是
     *  「点开列表再点支持」，`mineItems` 是同一件事发生在「我的反馈」页上——漏掉它
     *  的表现是那一页的支持按钮点了没反应（`toggleSupport` 找不到卡片就直接 return，
     *  连错误都不报）。详情那一份管的是深链进来的页面。
     *
     *  列表里没有就找详情：直接打开/刷新 `/feedback/<id>` 时三份列表都是空的
     *  （那一页不拉列表），而支持按钮只认这一份。 */
    _find(id: string): FeedbackCard | undefined {
      return (
        this.items.find((i) => i.id === id) ??
        this.adminItems.find((i) => i.id === id) ??
        this.mineItems.find((i) => i.id === id) ??
        (this.detail?.id === id ? this.detail : undefined)
      )
    },

    _patch(id: string, patch: Partial<FeedbackCard>): void {
      for (const list of [this.items, this.adminItems, this.mineItems]) {
        const card = list.find((i) => i.id === id)
        if (card) Object.assign(card, patch)
      }
      if (this.detail?.id === id) Object.assign(this.detail, patch)
    },

    /** 当前详情**并且**是这条时才返回它：慢响应回来时页面可能已经换了一条。 */
    _detailIfCurrent(id: string): FeedbackDetail | null {
      return this.detail && this.detailId === id ? this.detail : null
    },

    /** 楼里的一条评论。评论**只存在于详情那一份数据里**（卡片上只有条数），所以上面
     *  那两个动作都从这里拿 —— `_find` / `_patch` 管的是反馈级的字段，它们看不见
     *  `detail.thread`，拿它们改评论会静默什么都不做：按钮按得下去、有按下效果、
     *  数字一动不动，控制台里一句话也没有。 */
    _commentOf(id: string, commentId: string): FeedbackComment | undefined {
      return this._detailIfCurrent(id)?.thread.find((c) => c.id === commentId)
    },
  },
})
