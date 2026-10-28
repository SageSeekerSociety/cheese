/**
 * 「反馈详情页」那一组在预览站里的条目。
 *
 * 反馈详情页右侧那一栏拆出来的几件：处理人、一条评论、整栋评论楼、进展时间线。它们
 * 以前只有把着一整条反馈、连同它的评论和状态一起，才看得见；拆开之后各自只吃 props、
 * 只往上发事件，于是能单独摆在预览站里。走的是各组件 `computed` 真会有的岔路：能领还
 * 是不能领、点过赞没有、手上几条回复对服务端几条、走到梯子的哪一步。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogRoom.ts`、
 * `catalogSkills.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `FEEDBACK_ENTRIES`
 * 这个值，运行时不构成循环。数据见 `catalogFeedbackFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  building,
  CENTER_ROWS,
  DECLINED,
  DEPLOYED_WITH_NOTE,
  FEEDBACK_META,
  JUST_RECEIVED,
  LADDER,
  LIKED_COMMENT,
  REPLY_COMMENT,
  TOP_COMMENT,
} from './catalogFeedbackFixtures'

import FeedbackClaimCard from '@/components/feedback/FeedbackClaimCard.vue'
import FeedbackCommentItem from '@/components/feedback/FeedbackCommentItem.vue'
import FeedbackCommentsThread from '@/components/feedback/FeedbackCommentsThread.vue'
import FeedbackStatusTimeline from '@/components/feedback/FeedbackStatusTimeline.vue'
import FeedbackCenterPageView from '@/views/feedback/FeedbackCenterPageView.vue'
import FeedbackMinePageView from '@/views/feedback/FeedbackMinePageView.vue'

/** 按钮（`BaseButton` 是 `v-btn`）、点赞那颗 `v-icon`、回复框 `v-textarea`：都要 Vuetify。
 *  句子里的 @名字、时间、梯子上的字走的是全局的 `t()`，单独的 i18n 实例不用装。 */
const UI: CatalogNeed[] = ['vuetify']

/** 画面只发事件（动作都在容器里），预览站上不给动作：这几格要看的是它长什么样，
 *  不是按下去发生什么。 */
const noop = () => {}

/** 评论条（和渲染它的整栋评论楼）模板里有 `<i18n-t>`：「回复 X」那一行。它是 i18n
 *  插件注册的全局组件，所以这两件还要装 i18n —— 光有全局 `t()` 不够。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

export const FEEDBACK_ENTRIES: CatalogEntry[] = [
  {
    id: 'feedback-claim-card',
    title: 'FeedbackClaimCard',
    about:
      '反馈详情右栏的「处理人」一格：谁领着这条，以及这个读者能不能领取 / 放弃。两个按钮画不画全看服务端的 canClaim / canRelease。',
    file: 'src/components/feedback/FeedbackClaimCard.vue',
    component: FeedbackClaimCard,
    needs: UI,
    states: [
      {
        name: '没人领着，能领',
        note: '服务端说这个读者能领：左边写「还没人领取」，右边一颗描边的「领取」——按钮出不出来由服务端说了算，前端不自己判「我是不是持有人」。',
        props: { holder: null, canClaim: true, canRelease: false, busy: false },
        expect: '还没人领取',
      },
      {
        name: '持有人自己看，能放弃',
        note: '写着是谁领着（@handle 那颗 chip），这一格能按的是「放弃」，不是「领取」。',
        props: { holder: 'ana', canClaim: false, canRelease: true, busy: false },
        expect: '放弃',
      },
      {
        name: '别人领着',
        note: '两个按钮一个都不画：这里只能说出是谁领着，没有这个读者能做的事。',
        props: { holder: 'bob', canClaim: false, canRelease: false, busy: false },
        expect: 'bob',
      },
    ],
  },
  {
    id: 'feedback-comment-item',
    title: 'FeedbackCommentItem',
    about: '一条评论：谁写的、什么时候、有多少人赞、回的是谁。顶层评论和楼内回复共用这一件，差异只走参数。',
    file: 'src/components/feedback/FeedbackCommentItem.vue',
    component: FeedbackCommentItem,
    needs: UI_T,
    states: [
      {
        name: '别人赞过，还没点',
        note: '点赞是中性色：外框图标 + 文案「赞」+ 计数单独一格，只在有人赞过的时候才出现 —— 「赞 0」里的 0 会被读成「有人踩过」。',
        props: { comment: TOP_COMMENT, replying: false, isReply: false, replyCount: 0 },
        expectSelector: '.mdi-thumb-up-outline',
      },
      {
        name: '自己点过',
        note: '激活态是三个信号一起变（图标实心、文案从「赞」变「已赞」、底色出现），颜色只是其中之一 —— 只靠底色的话，色觉障碍的读者看不出自己点没点过。',
        props: { comment: LIKED_COMMENT, replying: false, isReply: false, replyCount: 0 },
        expectSelector: '.mdi-thumb-up',
      },
      {
        name: '楼内回复，指代谁',
        note: '服务端把 `reply_to_handle` 存下来了，所以单独一行画「回复 @谁」。顶层评论恒为 NULL，那一样它一个字都不画 —— 客户端猜不出来「回的是谁」。',
        props: { comment: REPLY_COMMENT, replying: false, isReply: true, replyCount: 0 },
        expect: '回复 alice',
      },
    ],
  },
  {
    id: 'feedback-comments-thread',
    title: 'FeedbackCommentsThread',
    about:
      '一栋评论楼：两层折叠，回复先露两条、其余折起来。楼下的按钮在「展开更多」和「加载更多」之间选，靠的是服务端的 reply_count。',
    file: 'src/components/feedback/FeedbackCommentsThread.vue',
    component: FeedbackCommentsThread,
    needs: UI_T,
    states: [
      {
        name: '一栋楼，回复折着',
        note: '手上已经拿到五条回复，默认只露最早的两条：楼下那个按钮是「展开更多 3 条回复」——摊开手上这几条，不发请求；「加载更多」说的是服务端那边还有。',
        props: { comments: building(5, 5), hasMore: false, loadingMore: false, loadingReplies: {} },
        expect: '展开更多 3 条回复',
      },
      {
        name: '服务端还有没取的',
        note: '手上两条、服务端说一共五条：按钮换成一个要去取下一页的「加载更多回复（还有 3 条）」。两个数一比就有答案，客户端不猜。',
        props: { comments: building(5, 2), hasMore: false, loadingMore: false, loadingReplies: {} },
        expect: '加载更多回复（还有 3 条）',
      },
      {
        name: '一条评论都没有',
        note: '空态是和别处同一副骨架的主副两句（「暂无评论 / 还没有人回复。」），不是一行灰字 —— 这是要人读的一句话。',
        props: { comments: [], hasMore: false, loadingMore: false, loadingReplies: {} },
        expect: '暂无评论',
      },
    ],
  },
  {
    id: 'feedback-status-timeline',
    title: 'FeedbackStatusTimeline',
    about:
      '反馈详情右侧那根竖着的时间线：已收录 → 处理中 → 已修复 → 已上线。画的是整架梯子，不是只画已经走过的那几步。',
    file: 'src/components/feedback/FeedbackStatusTimeline.vue',
    component: FeedbackStatusTimeline,
    // 一个 Vuetify 组件都不画：走过的是实心点、当前那一步额外加一圈、还没到的是空心，
    // 全靠 CSS。时间、档位名、@名字走全局的 t()，所以一件都不用装。
    needs: [],
    states: [
      {
        name: '刚收录，后面还没到',
        note: '梯子整架画出来：第一步是当前（实心 + 外圈），后面三档是空心的「未开始」。少了这个区分，一条刚收录的反馈右侧只有一个孤零零的点，读的人看不出后面还有几关。',
        props: { timeline: JUST_RECEIVED, status: 'received', ladder: LADDER },
        expect: '未开始',
      },
      {
        name: '一步按到已上线',
        note: '部署管线推的那一步没有人，靠一句说明说清是哪次改动：说明里的 PR 地址画成可点的 <a>。中间那两档已经过去了却没单独留下时间，写的是「无记录」，不是「未开始」。',
        props: { timeline: DEPLOYED_WITH_NOTE, status: 'deployed', ladder: LADDER },
        expect: '已由 PR #4321 修复并上线',
      },
      {
        name: '不修复',
        note: '「不修复」不在梯子上 —— 它是另一种结局。梯子画到它就停，后面的「已修复 / 已上线」不画，免得读的人以为还会轮到它们；走过去的那一档换成「已处理」。',
        props: { timeline: DECLINED, status: 'declined', ladder: LADDER },
        expect: '不修复',
      },
    ],
  },
  {
    id: 'feedback-center-page-view',
    title: 'FeedbackCenterPageView',
    about:
      '反馈中心首页的画面那一半：页头、栏位页签、一排筛选药丸、列表。取数、栏位、四个筛选、点支持全在容器 `feedback/FeedbackCenterPage.vue` 里（它调 store、读地址），这一半只收 props、只发事件，所以能脱离后端单独摆出来。',
    file: 'src/views/feedback/FeedbackCenterPageView.vue',
    component: FeedbackCenterPageView,
    needs: ['vuetify', 'router'],
    // 每格默认一份「有四条、一页到底」的世界；哪一格要看别的样子就在它自己的 props 里改。
    args: {
      isAdmin: false,
      tab: 'all',
      tabs: [
        { value: 'all', label: '全部', count: 4 },
        { value: 'hot', label: '热门', count: 2 },
        { value: 'resolved', label: '已完成', count: 1 },
      ],
      meta: FEEDBACK_META,
      query: '',
      filterKind: null,
      filterStatus: null,
      filterDays: null,
      filterAuthor: '',
      hasFilters: false,
      items: CENTER_ROWS,
      total: 4,
      loading: false,
      loadingMore: false,
      listHasMore: false,
      error: null,
      hotHalfLifeDays: 2,
      hotThreshold: 10,
      hotMinItems: 5,
      // 画面只发事件，动作都在容器里；预览站上不给动作（回题列表要看的不是按下去发生
      // 什么，而是这一格长什么样）。
      search: noop,
      'set-kind': noop,
      'set-status': noop,
      'set-days': noop,
      'set-author': noop,
      'clear-filters': noop,
      'set-tab': noop,
      retry: noop,
      more: noop,
      'dismiss-error': noop,
      support: noop,
    },
    states: [
      {
        name: '一屏反馈',
        note: '页头三颗动作、四条栏位页签（各带计数）、一排「不限」的筛选药丸，下面是列表：标题一行、摘要两行、底行是「哪一类、谁提的、多少人参与、走到哪一步」。',
        props: {},
        expect: '导出一个月的数据要等四十秒',
      },
      {
        name: '个人奖励',
        note: '侧栏只显示实际获奖励的反馈数与累计点数，提交不等于获奖。',
        props: {
          beta: {
            credits: 500,
            claimed: true,
            feedback_credits: 100,
            plan_credits: 1000,
            invite_credits: 150,
            invitee_credits: 150,
          },
          rewards: { feedback_count: 3, feedback_credits: 300, invite_credits: 0, items: [] },
        },
        expect: '我的反馈奖励',
      },
      {
        name: '管理员看得见入口',
        note: '服务端说这个读者是管理员：页头多一颗「管理后台」——它出不出现由服务端答，不是一个可以拨的开关。',
        props: { isAdmin: true },
        expect: '管理后台',
      },
      {
        name: '热门那一栏',
        note: '切到热门：页签下面多一句「为什么这么排」的说明（半衰期、门槛、最少几条都由服务端发下来），免得读者把它当成按支持数排。',
        props: { tab: 'hot' },
        expect: '按支持数算',
      },
      {
        name: '筛选之后没有',
        note: '被筛选（或栏位）收窄之后一条都没有：说的是「没有符合条件的反馈」并给一颗清除筛选的按钮，不是「暂无反馈」——那读起来像平台一条都没有。',
        props: { items: [], total: 0, hasFilters: true, filterKind: 'other' },
        expect: '没有符合条件的反馈',
      },
      {
        name: '搜索没结果',
        note: '搜过之后一条都没有：换一句关键词，而且说清搜索只覆盖哪几个字段，免得人以为该搜到的东西丢了。',
        props: { items: [], total: 0, query: '导出' },
        expect: '没有匹配的反馈',
      },
      {
        name: '拉挂了',
        note: '服务端那句话摆在空态里，动作是重试；上面那句写的是「这一次为什么没成」，下面那句是服务端的原话。',
        props: { items: [], total: 0, error: '网络不通' },
        expect: '反馈加载失败',
      },
      {
        name: '正在读',
        note: '还没到货：画骨架行（不是四条空行），标题和摘要的位置都占着，到货那一刻不会跳。',
        props: { items: [], total: 0, loading: true },
        expectSelector: '.skel--feedback',
      },
    ],
  },
  {
    id: 'feedback-mine-page-view',
    title: 'FeedbackMinePageView',
    about:
      '「我的反馈」的画面那一半：说明一句、列表、空态。清单边界（我提的 / 我替谁提的 / 指派给我的）和把未读游标往前推都在容器 `feedback/FeedbackMinePage.vue` 里，这一半只收 props、只发事件。',
    file: 'src/views/feedback/FeedbackMinePageView.vue',
    component: FeedbackMinePageView,
    needs: ['vuetify', 'router'],
    args: {
      items: CENTER_ROWS.slice(0, 2),
      total: 2,
      loading: false,
      loadingMore: false,
      hasMore: false,
      error: null,
      'empty-action': noop,
      more: noop,
      'dismiss-error': noop,
      support: noop,
    },
    states: [
      {
        name: '我的几条',
        note: '说明里带上总数（只在拉到之后才说），下面两行：同一条反馈在哪一页都长一样。',
        props: {},
        expect: '共 2 条。',
      },
      {
        name: '还没提过',
        note: '一条都没有：「你还没有提过反馈，也没有指派给你的」加一颗「提交一条」——这是这一页特有的边界，反馈中心那句「你提交的反馈会出现在这里」在这里是错的。',
        props: { items: [], total: 0 },
        expect: '你还没有提过反馈，也没有指派给你的',
      },
      {
        name: '拉挂了不冒充空',
        note: '没拉到：摆服务端那句话和重试，**不**说「还没有提过」——把一次网络故障说成「平台没有你的数据」是另一类 bug。',
        props: { items: [], total: 0, error: '「我的反馈」加载失败' },
        expect: '「我的反馈」加载失败',
      },
      {
        name: '还有下一页',
        note: '手上两条、服务端说一共二十条：列表尾巴上一颗「加载更多」加「已显示 2 / 20」。',
        props: { total: 20, hasMore: true },
        expect: '加载更多',
      },
    ],
  },
]
