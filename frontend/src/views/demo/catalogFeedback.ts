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
  DECLINED,
  DEPLOYED_WITH_NOTE,
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

/** 按钮（`BaseButton` 是 `v-btn`）、点赞那颗 `v-icon`、回复框 `v-textarea`：都要 Vuetify。
 *  句子里的 @名字、时间、梯子上的字走的是全局的 `t()`，单独的 i18n 实例不用装。 */
const UI: CatalogNeed[] = ['vuetify']

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
        expect: '@bob',
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
        expect: '回复 @alice',
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
]
