/**
 * 组件预览站里「对话栏那一组」的条目。
 *
 * 这四件是 ChatPanel 拆出来、只管画的那几件：话题头、整条时间线、新消息药丸、
 * 出错提示条。它们和别的条目没有两样，单独放一份只是因为 `frontend/src` 下的文件
 * 有一千行的上限，而 `catalog.ts` 是一份会一直长下去的注册表。条目的规矩（这是什
 * 么 / 在哪儿 / 需要哪几样 / 看哪几格）见 `catalog.ts`；数据见 `catalogFixtures.ts`。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `CHAT_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { AGENT_NAME, CHAT_ROWS, CHAT_TOPIC, chatTimelineProps } from './catalogFixtures'

import ChatErrorToast from '@/components/chat/ChatErrorToast.vue'
import ChatNewMessagesPill from '@/components/chat/ChatNewMessagesPill.vue'
import ChatPanelHeader from '@/components/chat/ChatPanelHeader.vue'
import ChatTimeline from '@/components/chat/ChatTimeline.vue'
import MessageQuote from '@/components/room/MessageQuote.vue'
import { topicStateBadge } from '@/lib/topicState'

const UI: CatalogNeed[] = ['vuetify']
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 话题头那几格共用的几样：状态标由 `lib/topicState.ts` 算，和产品里是同一张表。 */
const HEADER_BASE = {
  topic: CHAT_TOPIC,
  connected: true,
  prHeader: false,
  hideHeader: false,
  prShortId: '128',
  prState: topicStateBadge('open'),
  titleOverride: null as string | null,
  backLabel: null as string | null,
}

// ---- 对话栏（ChatPanel）拆出来的那一组「只管画」的件 -------------------------
//
// 拆之前这四件都长在 ChatPanel 里。现在每一件都是 props 进、事件出——于是「能单独
// 渲染」在预览站里是一条每次跑测试都要重新过的机械结论，而不是「它应该能」。
export const CHAT_ENTRIES: CatalogEntry[] = [
  {
    id: 'message-quote',
    title: 'MessageQuote',
    about: '问题附带的原始资料：展开核对页码、来源、版本与原文。',
    file: 'src/components/room/MessageQuote.vue',
    component: MessageQuote,
    needs: ['i18n'],
    states: [
      ...(['committed', 'live'] as const).map((source) => ({
        name: source === 'committed' ? '已提交的页' : '现场的页',
        note: '资料中的名字与路径照原文显示，正文空白也原样保留。',
        props: {
          quote: {
            kind: 'slide-page',
            path: 'slides/@评审 <@cheese-other>.pptx ',
            source,
            version: 'v7',
            task_id: null,
            page: 2,
            text: '  @评审 <@cheese-other>\n原始页面文字。\n',
          },
        },
        expect: '引用第 2 页文字',
      })),
      {
        name: '页里选中的一段',
        note: '`scope` 说正文是页里的一段而不是整页，展开那句话跟着换。',
        props: {
          quote: {
            kind: 'slide-page',
            path: 'deck.pptx',
            source: 'committed',
            version: 'v7',
            task_id: null,
            page: 2,
            scope: 'selection',
            text: '选中的这一句',
          },
        },
        expect: '引用第 2 页里选中一段',
      },
    ],
  },
  {
    id: 'chat-panel-header',
    title: 'ChatPanelHeader',
    about: '话题头：工作话题是「话题 = PR」那一套（编号 + 状态标），私聊和项目本体是普通那一套。',
    file: 'src/components/chat/ChatPanelHeader.vue',
    component: ChatPanelHeader,
    needs: UI,
    states: [
      {
        name: '工作话题（PR 形态）',
        note: '编号和状态标都来自 props：`prShortId` 是短号，`prState` 是 lib/topicState 算出来的那两个字。',
        props: { ...HEADER_BASE, prHeader: true },
        expect: '进行中',
      },
      {
        name: '私聊（普通形态）',
        note: '没有 PR 那一套，只剩标题和连接状态。左边那颗 ← 是给「点进来的名册页不在侧栏」的地方退回去用的。',
        props: {
          ...HEADER_BASE,
          titleOverride: '爱丽丝',
          backLabel: '返回名册',
          connected: false,
        },
        expect: '返回名册',
      },
      {
        name: '已采纳',
        note: '同一件东西的另一个状态：标是「已采纳」，颜色由 `prState.cls` 决定。',
        props: { ...HEADER_BASE, prHeader: true, prState: topicStateBadge('archived') },
        expect: '已采纳',
      },
    ],
  },
  {
    id: 'chat-timeline',
    title: 'ChatTimeline',
    about: '对话本身：滚动的那一栏、每一行（消息 / 通知）、日期线、未读线、悬停条、翻页加载器、起手区块。',
    file: 'src/components/chat/ChatTimeline.vue',
    component: ChatTimeline,
    needs: UI_T,
    states: [
      {
        name: '一段对话',
        note: '整段由 props 摆出来：行、每行的分组关系、日期线都在外面算好。这一格是预览站里唯一能看到「一整屏对话」长什么样的地方。',
        props: chatTimelineProps(),
        expect: '王长鑫',
      },
      {
        name: '空房间',
        note: '一条都没有：不画骨架、不画加载器，就是一栏空的——新话题第一次打开时就是这样。',
        props: chatTimelineProps({ rows: [] }),
      },
      {
        name: '加载更早的消息',
        note: '上面还有更早的一页：这一行在取数期间也照样画，否则它一出现就把读的人往下推。',
        props: chatTimelineProps({ hasMore: true, loadingOlder: true }),
        expect: '加载更早的消息',
      },
      {
        name: '以下是新消息',
        note: '开话题时按当时的未读数往回数一次就冻住的那条线：新的从哪儿开始。',
        props: chatTimelineProps({ unreadAnchorId: CHAT_ROWS[1]?.block.id ?? null }),
        expect: '以下是新消息',
      },
      {
        name: '还没人说过话',
        note: '芝士还没开口：起手区块递上三件具体的事，点一下就是一句现成的话。',
        props: chatTimelineProps({
          rows: [],
          showStarters: true,
          agentSeat: { handle: 'cheese', label: '芝士' },
          agentName: AGENT_NAME,
          starterPrompts: [
            { label: '查一份资料', text: '帮我查一下这个项目的背景' },
            { label: '起一份文档', text: '把刚才说的整理成一份文档' },
          ],
        }),
        expect: '从一件具体的事开始',
      },
    ],
  },
  {
    id: 'chat-new-messages-pill',
    title: 'ChatNewMessagesPill',
    about: '浮在时间线底部的那颗药丸：只要不在底部就挂着，往上翻着的时候数来了几条，没有新消息时说「回到最新」。',
    file: 'src/components/chat/ChatNewMessagesPill.vue',
    component: ChatNewMessagesPill,
    needs: UI_T,
    states: [
      {
        name: '来了几条新的',
        note: '数字是滚出来的（RollingNumber），点了就回到最新：`jump` 事件交给上面决定去哪。',
        props: { count: 3, hasNewer: false, atBottom: false },
        expect: '条新消息',
      },
      {
        name: '停在历史中间',
        note: '这时候底下只是这一段的底，不是最新的底：写「回到最新」，不写条数。',
        props: { count: 0, hasNewer: true, atBottom: false },
        expect: '回到最新',
      },
      {
        name: '翻上去看历史',
        note: '只是往上翻了翻、没有新消息：入口照样在，写「回到最新」——它不再是一颗要等新消息才出现的按钮。',
        props: { count: 0, hasNewer: false, atBottom: false },
        expect: '回到最新',
      },
      {
        name: '停在底部',
        note: '在底部（或离底部不到 80px）：它整颗收起来，只在时间线上留一个零高度的锚点（这一格没有字，画出来就行）。',
        props: { count: 0, hasNewer: false, atBottom: true },
      },
    ],
  },
  {
    id: 'chat-error-toast',
    title: 'ChatErrorToast',
    about: '出错提示：从底部升起来，点右边的叉关掉。新的一条直接顶替旧的，不排队。',
    file: 'src/components/chat/ChatErrorToast.vue',
    component: ChatErrorToast,
    needs: UI,
    states: [
      {
        name: '一条错误',
        note: '话就是 `message` 本身（取数失败、发不出去……），组件不加也不改一个字。',
        props: { message: '这条消息没能发出去' },
        expect: '这条消息没能发出去',
      },
      {
        name: '没有错误',
        note: '`message` 是 null：什么都不画。',
        props: { message: null },
      },
    ],
  },
]
