/**
 * 反馈 store 的 state：这一层手里那份数据长什么样，以及它们的初值。
 *
 * 初值写成 `initialState()` 而不是一个常量对象，是 Pinia 要的：每条 store 实例各拿
 * 一份自己的，共用一个对象会让两个实例串数据。类型由它自己推出来（`FeedbackState`），
 * 所以下面的 getters 和各个动作模块说的都是同一份，不必再抄一遍字段。
 */

import type { StatsDays, StatsKind, StatsShapes } from '@/api'
import type {
  FeedbackCard,
  FeedbackCounts,
  FeedbackDetail,
  FeedbackKind,
  FeedbackMeta,
  FeedbackStatus,
} from '@/cx_types'
import type { AdminSort, AdminTab, FeedbackTab } from './types'

import { EMPTY_COUNTS, emptyDraft } from './helpers'

/** 看板那三份数据各存各的：分类 → 它那一份（还没拉到就是 null）。 */
type StatsBucket = { [K in StatsKind]: StatsShapes[K] | null }

/** 把刚拉到的那一份写进它自己那一格。
 *
 *  为什么不直接 `bucket[kind] = value`：那是**联合索引写入**，TS 没法保证写进去的正是
 *  那一格要的类型（三格的形状互不相同），它会拒绝。三个分支各自窄化一次，是对同一件
 *  事的显式说法 —— 也正是「切到用量却把反馈的数写进用量那一格」这类错会藏身的地方。 */
export function assignStats(bucket: StatsBucket, kind: StatsKind, value: StatsShapes[StatsKind]): void {
  if (kind === 'feedback') bucket.feedback = value as StatsShapes['feedback']
  else if (kind === 'usage') bucket.usage = value as StatsShapes['usage']
  else if (kind === 'platform') bucket.platform = value as StatsShapes['platform']
  else if (kind === 'pipeline') bucket.pipeline = value as StatsShapes['pipeline']
  else if (kind === 'product') bucket.product = value as StatsShapes['product']
  else if (kind === 'integrations') bucket.integrations = value as StatsShapes['integrations']
  else bucket.performance = value as StatsShapes['performance']
}

export function initialState() {
  return {
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
     *  存的是深链上的**原文**（'7d' 这样的相对窗口，或一个日期）——地址栏和筛选
     *  chip 都要拿它原样说话；发给服务端时才折成日期（见 `loadAdmin` 与
     *  `lib/feedbackWindows.ts`）。 */
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
       但它决定了下一个请求打哪条接口，所以由 store 记着，页面重挂载时不会跳回第一类。
       默认落点是**交付**（pipeline）：管理员早上第一个问题是「现在该我动的是哪几件」。 */
    statsKind: 'pipeline' as StatsKind,
    /** 看板的统计窗口（7/30/90 天）。和 `statsKind` 同一个理由放 store：它决定请求
     *  的 `days` 参数，而 R 键（`AdminLayout`）那一路 `loadStats()` 不带参数 —— 窗口
     *  写死成默认 7 的话，30 天窗口下按 R 会把数据悄悄拉回 7 天。 */
    statsDays: 7 as StatsDays,
    /** 每一类各自**成功**那一刻（epoch ms，null = 还没成功过）—— 页头那句「更新于
     *  HH:MM」读它。失败**不动**旧时间戳：旧数据继续配旧时刻展示，不能因为重试失败
     *  就把它刷成「刚更新」。 */
    statsAt: {
      feedback: null,
      usage: null,
      platform: null,
      performance: null,
      pipeline: null,
      product: null,
      integrations: null,
    } as Record<StatsKind, number | null>,
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
  }
}

export type FeedbackState = ReturnType<typeof initialState>
