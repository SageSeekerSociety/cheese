/**
 * 反馈这一层**藏在模块里**的那份状态：请求序号、防抖定时器、几份缓存，以及「这条
 * 推过已读游标没有」。
 *
 * 它们**不在 state 里**：一个自增的数字和定时器 id 都不是界面状态，放进 state 只会
 * 让 Vue 去追踪一个没人渲染的值；缓存更不能进 —— 一个存下来的数字没法回答「它现在
 * 还是不是真的」。
 *
 * 收进**一个对象**而不是各自一个 `let`，是因为这一层已经拆成好几个模块：ESM 里别的
 * 模块改不了本模块的 `let` 绑定，而这些字段本来就是同一件东西 —— 「这一层替上一个人
 * 记着的东西」，读写它们的模块共用这一份。`resetFeedbackCaches()` 一次把它们全丢掉。
 */

import type { StatsKind } from '@/api'
import type { FeedbackCard, FeedbackDetail } from '@/cx_types'

export const session = {
  /** 请求序号，用来丢掉过期响应。每个序号只管**自己那条路**（这一个列表的先后两次
   *  请求谁算数）。计数那条路没有属于自己的序号 —— 它跨路，用的是下面 `countsRev`
   *  那一把。 */
  listSeq: 0,
  adminSeq: 0,
  mineSeq: 0,
  /** 看板那三类的代次，**一类一把**。
   *
   *  一把共用的计数器在这里是错的，而且错得很安静：看板挂载时就发了一次请求，人接着
   *  切到另一类 —— 共用计数器一自增，那一份还在飞的响应就被判成「过期」，于是第一类
   *  永远停在 `null`，切回去还要再拉一次。而「过期」在这里的**真实含义**只是「后来又
   *  问了同一类」，切到别的类并没有让谁过期。 */
  statsSeq: {
    feedback: 0,
    usage: 0,
    platform: 0,
    performance: 0,
    pipeline: 0,
    product: 0,
    integrations: 0,
  } as Record<StatsKind, number>,
  /** 管理端那一条详情的代次。它的两次操作会在**同一个 id** 上相遇（读一次、写完再回
   *  一次），所以「id 一样」不足以判断一份响应还算不算数 —— 见 `loadAdminDetail` 与
   *  `_adminWrite`。 */
  adminDetailSeq: 0,
  searchTimer: null as ReturnType<typeof setTimeout> | null,
  /** 表单落盘的防抖定时器，见 `touchDraft`。 */
  draftTimer: null as ReturnType<typeof setTimeout> | null,
  /** 管理台搜索框那一个。和 `searchTimer` 分开，因为两个框可能在同一个浏览器标签页里
   *  先后被用过（先进反馈中心搜一下，再进管理台搜一下）—— 共用一支定时器时，后者会把
   *  前者还没发出去的那次输入取消掉，而那次输入属于另一页。 */
  adminQueryTimer: null as ReturnType<typeof setTimeout> | null,
  /** 已经推过未读游标的 id。它决定的是「这次还要不要发请求」，不是一个被渲染的值，
   *  放进 state 只会让 Vue 去追踪一个没人看的集合。 */
  markedReadIds: new Set<string>(),
  /** 计数的一把**单调修订号**，全仓库只有它一个来源。
   *
   *  `counts` 有好几条路会写：公开列表、管理端列表、`/feedback/counts` 那个轻量接口，
   *  以及两个**本地动作**（推已读游标）。它们发出的时间有先后，回来的时间却没有 ——
   *  先发的后到，屏幕上的数字就会从一个较新的值跳回一个较旧的。所以每个想提交
   *  counts 的人在**发出请求那一刻**领一个号，回来时带着它；`_commitCounts` 只认号
   *  大的。这样「谁算数」只由发出去的时刻决定，与到达顺序无关。
   *
   *  和上面那几个序号分开：那些是**每条路自己**的（同一个列表的先后两次请求谁算数），
   *  这一把是**跨路**的（列表的响应和推游标谁算数）。混成一把是错的：一次 `loadList`
   *  会把之后发出的 `refreshCounts` 一起顶掉。 */
  countsRev: 0,
  /** 公开列表上一次的那一页，连同它的**问题指纹**（Tab + 搜索词）。指纹命中就是
   *  「问的还是同一个问题」，那就先把手上这份画出来、背后再重拉一次 —— 骨架是「我还
   *  不知道」的表示，而这里明明知道，从详情页返回时不该再闪一次。
   *
   *  `counts` **不许进缓存**（见 `_commitCounts`）：一个存下来的数字没法回答「它现在
   *  还是不是真的」，把上一分钟的未读数再画一遍，比先空着更糟。 */
  listCache: null as { key: string; items: FeedbackCard[]; total: number } | null,
  /** 「我的反馈」那一页。它没有参数，所以指纹就是它自己。
   *
   *  这一份要**跟着身份走**：它装的是「我提的 / 指派给我的」，换个人登录之后留着就是
   *  上一个人的清单。`resetFeedbackCaches()` 负责，别在别处再清一遍。 */
  mineCache: null as { items: FeedbackCard[]; total: number } | null,
  /** 详情缓存：id → 上一次从 `GET /feedback/{id}` 拿到的那条。
   *
   *  **只让用户侧那一条路碰它**。管理端的 `loadAdminDetail` 回的是同一个类型，可见性
   *  判据却是另一个（管理员 vs 并集），把它的响应写进来就等于让同一台机器上的下一个
   *  读者先画出一份只有管理员能看的内容 —— 那不是「旧了一点」。
   *
   *  存的是**和 `state.detail` 同一个对象**：`_patch` 就地改字段（点赞、支持数）时，
   *  缓存里那份一起跟着变，不需要任何写回。反过来说，谁把 `state.detail` **整个换成
   *  另一个对象**（`loadDetail` 的响应、`_adminWrite` 的响应），就得自己决定要不要更新
   *  缓存 —— 见这两处的注释。 */
  detailCache: new Map<string, FeedbackDetail>(),
  /** 这个人在详情上写成了几笔（支持、评论、点赞、删评论）。每一笔的回答都**就地**改
   *  详情，所以一次在它之前出发的 `loadDetail` 回来时，手上是写之前的那份 —— 拿它整个
   *  换掉，刚点的支持数就退回去了。`loadDetail` 出发前记下这个数，回来时对不上就再读
   *  一次：服务端在回答写之前已经提交，第二次读得到它。 */
  detailWrites: 0,
}

/** 计数那把号：谁想提交 `counts`，就在**发出请求那一刻**领一个（见 `session.countsRev`）。 */
export const takeCountsRev = (): number => ++session.countsRev

/** 丢掉两份列表缓存。**不动已经在屏幕上的数组** —— 「屏幕上那份要不要清」是另一个
 *  决定（`submit` 清了，因为它刚插进去一条），这里只回答「下次还信不信缓存」。两件事
 *  分开写，是因为它们真的会分开：提交之后屏幕上那份该清，而缓存里那份对同一个查询仍
 *  在的回答是错的。 */
export function dropListCaches(): void {
  session.listCache = null
  session.mineCache = null
}

/** 把这一层替**上一个人**记着的东西全部丢掉。退出登录、以及换成另一个人登录时调
 *  （见 `services/account.ts`）。
 *
 *  公开列表的那一份也要丢：私密反馈不在里面，但「这台机器上刚刚翻过哪一页反馈」仍然
 *  是那个人的痕迹，而 `/feedback` 对谁都是同一页。
 *
 *  **为什么清缓存就够了，`state` 不用一起清**：三个页面都把内容挂在 loading 那道门后
 *  面（`<LoadingSkeleton v-if="…loading">` + `v-else` 才是内容，见 FeedbackCenterPage /
 *  FeedbackMinePage / FeedbackDetailPage）。`state` 里那份旧的在下一次挂载时会被自己
 *  那次请求翻成 loading=true、当场被骨架盖住；真正会把旧内容**画出来**的只有命中缓存
 *  这一条路 —— 它把 loading 留在 false 上，于是骨架让开、旧内容直接上屏，一直挂到重拉
 *  回来。所以这一关要卡在缓存上。 */
export function resetFeedbackCaches(): void {
  dropListCaches()
  session.detailCache.clear()
  // 这一份也要丢，理由和上面两份一样，只是它的内容是**谁**而不是**什么**：
  // `session.markedReadIds` 说的是「这个人的自动已读已经替这几条推过游标了」，留到
  // 换人之后就是拿上一个人的账本记下一个人 —— 下一个人点开同一条时那次自动已读会被
  // 静默跳过（不推游标、不清徽标，也没有任何提示）。
  session.markedReadIds.clear()
}
