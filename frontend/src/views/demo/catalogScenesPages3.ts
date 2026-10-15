/**
 * 预览站里「拆出来的那半张画面」第二批：两页各拆成一个容器（`X.vue` —— 读地址、取数、
 * 发请求）加一个画面（`XView.vue` / `XListView.vue` —— 只收 props、只发事件），另外还有
 * 那一页底下的答案卡片，它本来也跟着取数，现在同样只画状态。这里摆的是画面那一半，
 * 所以每一件都不带路由、不带接口就能单独渲染。
 *
 * 这一批有六件：空间分析那一层（页头、六格导航、筛选栏、正文换页）、一道题的全部回答、
 * 一张回答卡片，以及后来拆题目详情那一页时跟着出的三件 —— 题目详情的画面、单份回答的画面、
 * 弹窗里那份邀请名单。条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，
 * 规矩见 `catalog.ts`）。数据见 `catalogScenesPages3Fixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  ANALYTICS_FILTERS,
  answer,
  ANSWERS,
  CATEGORY_ITEMS,
  INVITE_PEOPLE,
  question,
  QUESTION_CONTENT_HTML,
  StubAnalyticsSection,
} from './catalogScenesPages3Fixtures'

import AnswerCard from '@/components/answer/AnswerCard.vue'
import InvitationList from '@/components/questions/InvitationList.vue'
import DetailAnswerListView from '@/views/question/DetailAnswerListView.vue'
import DetailAnswerView from '@/views/question/DetailAnswerView.vue'
import DetailView from '@/views/question/DetailView.vue'
import IndexView from '@/views/spaces/detail/analytics/IndexView.vue'

/** 三件都要 Vuetify 的画法和一套语言包（卡片、骨架屏、筛选栏、页头都在 Vuetify 上）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

export const SCENES_PAGES3_ENTRIES: CatalogEntry[] = [
  {
    id: 'answer-card',
    title: 'AnswerCard',
    about:
      '一张回答卡片：谁答的、正文两段、赞同/反对那一票和当前分数、评论与收藏。采纳、投票、收藏都是发请求的事，落在页面那边的 `composables/useAnswerActions` 里；这里只画收到的状态，把要点的事连同「是哪一份回答」交上去。「这个人能不能采纳」也由外面判好了递进来 —— 判据要读眼下登着的是谁。',
    file: 'src/components/answer/AnswerCard.vue',
    component: AnswerCard,
    needs: UI_T,
    args: {
      answer: answer(),
      question: question(),
      canAccept: false,
      defaultAvatarId: null,
    },
    states: [
      {
        name: '一条回答',
        note: '提问的人和别的人看是同一个样子：作者、正文、右边的分数，底下评论和收藏两颗。',
        props: {},
        expect: '因为瑞利散射',
      },
      {
        name: '提问的人看这一条',
        note: '登着的就是提问的人，而且这道题还没有采纳过：正文底下多一颗「采纳这个回答」。',
        props: { canAccept: true },
        expect: '采纳这个回答',
      },
      {
        name: '已经采纳过了',
        note: '这道题已经采纳过一条回答：那颗按钮整个不画 —— 采纳只有一次，第二颗是骗人的。',
        props: { canAccept: true, question: question({ accepted_answer: answer({ id: 12 }) }) },
        expect: '因为瑞利散射',
      },
      {
        name: '用的就是全局默认头像',
        note: '他没挑过头像，用的是平台那张默认的：不画图，走名字首字母；挑过的才画他自己那张。',
        props: { answer: answer({ author: { ...answer().author, avatarId: 9 } }), defaultAvatarId: 9 },
        expectSelector: '.v-avatar',
      },
    ],
  },
  {
    id: 'detail-answer-list-view',
    title: 'DetailAnswerListView',
    about:
      '「全部回答」那一页的画面：还没读回来时两行骨架屏，读回来是空的就画空板，否则一份回答卡片排下来。翻页、采纳/投票/收藏的请求和「这个人能不能采纳」都在容器 `question/DetailAnswerList.vue` 里，这里只收结果、把要点的事连同是哪一份回答交上去。',
    file: 'src/views/question/DetailAnswerListView.vue',
    component: DetailAnswerListView,
    needs: UI_T,
    args: {
      answers: ANSWERS,
      question: question(),
      refreshing: false,
      canAccept: false,
      defaultAvatarId: null,
    },
    states: [
      {
        name: '正在读',
        note: '第一页还在路上：画两行骨架屏，不画「还没有回答」—— 人还没问完就说没有，是把话说错了。',
        props: { refreshing: true, answers: [] },
        expectSelector: '.v-skeleton-loader',
      },
      {
        name: '这道题还没有回答',
        note: '读回来了，一条都没有：画空板（「空空如也...」那一种），不是一片空白。',
        props: { answers: [] },
        expect: '空空如也...',
      },
      {
        name: '两条回答，第二条收藏过了',
        note: '列表一份份卡片排下来；第二条已经收藏，那一颗写的是「取消收藏」。',
        props: { canAccept: true },
        expect: '取消收藏',
      },
    ],
  },
  {
    id: 'invitation-list',
    title: 'InvitationList',
    about:
      '「邀请谁来回答」弹窗里那份名单：一行一个人，右边一颗邀请按钮；已经邀请过的人那颗写「已邀请」，而且按不动。名单、已经邀请过谁、点下按钮发哪趟请求都在页面 `question/Detail.vue` 那边（那两趟读侧只有登录了才发），这里只收结果、把点的那个人交上去。',
    file: 'src/components/questions/InvitationList.vue',
    component: InvitationList,
    needs: UI_T,
    args: {
      people: INVITE_PEOPLE,
      invitedIds: [],
    },
    states: [
      {
        name: '两个人都还没邀请',
        note: '名单上两个人都不在「已经邀请过」那份里：两颗按钮都写「邀请回答」，都点得动。',
        props: {},
        expect: '邀请回答',
      },
      {
        name: '其中一个已经邀请过',
        note: '第一个人在「已经邀请过」那份里：他那颗按钮写「已邀请」，也按不动了 —— 推荐名单是全量档案，不比一下就会把点过的人再画成可点一次。',
        props: { invitedIds: [INVITE_PEOPLE[0].id] },
        expect: '已邀请',
      },
    ],
  },
  {
    id: 'detail-answer-view',
    title: 'DetailAnswerView',
    about:
      '单份回答那一页的画面：头上那颗「查看全部 N 条回答」，底下一张回答卡片；题或回答还没读回来时各自画骨架屏。读哪一份、这个人能不能采纳、采纳/投票/收藏的请求都在容器 `question/DetailAnswer.vue` 那边，这里只收结果、把要点的事连同是哪一份回答交上去。',
    file: 'src/views/question/DetailAnswerView.vue',
    component: DetailAnswerView,
    needs: UI_T,
    args: {
      questionData: question(),
      answerData: answer(),
      canAccept: false,
      defaultAvatarId: null,
    },
    states: [
      {
        name: '一份回答',
        note: '题目和回答都读回来了：头上那颗按钮带着这道题的回答总数，底下是这一份回答的卡片。',
        props: {},
        expect: '查看全部 2 条回答',
      },
      {
        name: '还没读回来',
        note: '两份都还在路上：按钮那行和卡片各画一段骨架屏，不是一片空白。',
        props: { questionData: null, answerData: null },
        expectSelector: '.v-skeleton-loader',
      },
      {
        name: '登着的是提问的人',
        note: '提问的人看自己这道题、而且还没采纳过：卡片底下多一颗「采纳这个回答」。',
        props: { canAccept: true },
        expect: '采纳这个回答',
      },
    ],
  },
  {
    id: 'detail-view',
    title: 'DetailView',
    about:
      '题目详情那一页的画面：题卡（标题、关注、话题、提问者、悬赏、正文、赞同/反对）、被采纳时的提示条、中间留给子页的一格、底下发回答的编辑器。读哪道题、关注/投票/加悬赏/发回答、以及邀请弹窗里那份名单，都在容器 `question/Detail.vue` 那边；中间那一格是被路由认出来的子页，由容器从插槽递进来。',
    file: 'src/views/question/DetailView.vue',
    component: DetailView,
    needs: UI_T,
    args: {
      loadFailed: false,
      loadError: null,
      questionData: question(),
      questionId: question().id,
      contentHtml: QUESTION_CONTENT_HTML,
      createdAt: '3 天前',
      updatedAt: '1 小时前',
      showUpdatedAt: false,
      authorAvatarUrl: '',
      bountyLoading: false,
      invitePeople: INVITE_PEOPLE,
      invitedIds: [],
      bountyDialog: false,
      addBountyInput: 3,
      inviteDialog: false,
    },
    states: [
      {
        name: '一道题',
        note: '读回来了：标题、发布/浏览那几段、关注按钮、话题、提问者、正文，底下是赞同/反对和评论收藏。中间那一格空着（预览站里没有子路由）。',
        props: {},
        expect: '为什么天空是蓝的',
      },
      {
        name: '还在读',
        note: '还在路上：整张卡片换成一格骨架屏，不是画一张空的题卡。',
        props: { questionData: null },
        expectSelector: '.v-skeleton-loader',
      },
      {
        name: '读失败了',
        note: '读失败和「还没读回来」是两件事：这一段带一句原因和一颗重试，不一直停在骨架上。',
        props: { loadFailed: true, loadError: 'Network Error' },
        expect: '无法加载问题',
      },
      {
        name: '挂着悬赏，还没采纳',
        note: '题目挂着悬赏、还没有被采纳：题卡底下多一颗「邀请回答」（名单就在它的弹窗里）和一颗「悬赏」。',
        props: {
          questionData: question({
            bounty: 10,
            has_bounty: true,
            bounty_start_at: Date.parse('2026-10-01T10:00:00+08:00') / 1000,
          }),
        },
        expect: '邀请回答',
      },
      {
        name: '已经被采纳过',
        note: '这道题采纳过了：题卡下面多一条提示，写清是哪位答的，并给一颗通往那条回答的按钮 —— 采纳过就不再画悬赏和邀请。',
        props: { questionData: question({ accepted_answer: answer({ id: 12 }) }) },
        expect: '的回答已被采纳',
      },
      {
        name: '已经回答过这道题',
        note: '这个人自己答过了：底下那块编辑器换成一句「点击查看」，不再让人重答一遍。',
        props: { questionData: question({ my_answer_id: 11 }) },
        expect: '你已经回答过该问题，点击查看',
      },
    ],
  },
  {
    id: 'space-analytics-index-view',
    title: 'IndexView',
    about:
      '空间分析那一层的画面：页头、六格导航、筛选栏，正文那一格换页时的淡入淡出。筛选条件落在地址上、分类列表是取来的、正文是哪一格由路由认 —— 这三件事都在容器 `spaces/detail/analytics/Index.vue` 里。筛选栏里改到一半的那一份也住在这里：地址上那份换了才跟上，改一半的不丢。',
    file: 'src/views/spaces/detail/analytics/IndexView.vue',
    component: IndexView,
    needs: UI_T,
    args: {
      page: StubAnalyticsSection,
      categoryItems: CATEGORY_ITEMS,
      filters: ANALYTICS_FILTERS,
    },
    states: [
      {
        name: '筛着条件的分析页',
        note: '正文那一格由路由认出来递进来（预览站给的是替身），上面是六格导航和筛选栏。',
        props: {},
        expect: '这一格是分析的正文',
      },
      {
        name: '正文那一格还没认出来',
        note: '路由还没配上（或者这一格下线了）：导航和筛选栏照旧在，正文那块空着，不报错。',
        props: { page: null },
        expectSelector: '.analytics-layout',
      },
    ],
  },
]
