/**
 * 提问卡那三件（`AskFoldLine`、`AskQuickReplies`、`AskGroupCard`）在预览站里的条目。
 * 单放一份是因为 `catalogRoom.ts` 顶着一千行的上限——和 `catalogChannelFiles.ts`、
 * `catalogChannelDialogs.ts` 同一批文件同一个理由。
 *
 * 数据编在这里：`askBlock` 是芝士问的一道题（`answer_log` 由各格给），
 * `groupQuestions` 是一次问的两道题（`AskGroupCard` 收成一张卡）。要摆出产品里真会
 * 出现的几种形状——刚问出来、被别人答了、自己答了、自己跳过了、提问方收回了。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { AGENT_NAME } from './catalogFixtures'

import AskFoldLine from '@/components/ask/AskFoldLine.vue'
import AskGroupCard from '@/components/ask/AskGroupCard.vue'
import AskQuickReplies from '@/components/ask/AskQuickReplies.vue'

const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 芝士问的一道题，`answer_log` 由各格给。 */
function askBlock(
  answerLog: Record<string, unknown>[],
  options: { text: string; explain?: string }[] = [
    { text: '按部门 (Recommended)', explain: '和去年的报表对得上' },
    { text: '按项目', explain: '每个项目一张表' },
  ]
) {
  return {
    id: 'demo-question',
    topic_id: 'demo-room',
    kind: 'message',
    author: 'cheese',
    author_type: 'participant',
    content: '预算按哪个口径统计？',
    created_at: '2026-10-05T09:00:00Z',
    meta: {
      options,
      asked: ['alice'],
      answer_log: answerLog,
    },
  }
}

/** 一次问的两道题（`AskGroupCard` 收成一张卡）：题签上写各自的标题，`answer_log` 由
 *  各格给第一道的。同组的两道题各自等人答——一道被答掉、被跳过，另一道照旧等。 */
function groupQuestions(firstLog: Record<string, unknown>[] = []) {
  const scope = askBlock(firstLog)
  const owner = {
    ...askBlock([]),
    id: 'demo-question-2',
    content: '谁来维护这份表？',
    meta: { ...askBlock([]).meta, options: [{ text: '我' }, { text: '芝士' }] },
  }
  return [
    { ...scope, meta: { ...scope.meta, header: '口径' } },
    { ...owner, meta: { ...owner.meta, header: '维护' } },
  ]
}

const GROUP_QUESTIONS = groupQuestions()

export const ASK_ENTRIES: CatalogEntry[] = [
  {
    id: 'ask-fold-line',
    title: 'AskFoldLine',
    about:
      '问别人的一道题在频道里收成的那一行：谁问、问谁、题目一句、等没等到答案（或已经答了什么）。点开才是整张提问卡，再点收起。问到你自己的题不走这一件——照整张卡画，因为你正要作答。提问方把题收回去了的也走这一件：那一行改成说「芝士撤回了」，题目照旧，点开看得到原样。',
    file: 'src/components/ask/AskFoldLine.vue',
    component: AskFoldLine,
    needs: UI_T,
    states: [
      {
        name: '收起来了',
        note: '一行 13px：谁问谁、题目、还没有回答。没有外框，箭头在右端；点整行展开。',
        props: {
          blocks: [askBlock([])],
          asker: AGENT_NAME,
          names: { alice: 'Alice' },
          refs: { mentionNames: { alice: 'Alice' }, topicTitles: {} },
          expanded: false,
          viewer: 'bob',
        },
        expectSelector: '.ask-fold__chevron',
      },
      {
        name: '点开了',
        note: '上面的那一行还在（收的作用），下面接的是问 alice 的那张完整的卡：选项、末行的输入框都在，灰着——问的不是看的人。',
        props: {
          blocks: [askBlock([])],
          asker: AGENT_NAME,
          names: { alice: 'Alice' },
          refs: { mentionNames: { alice: 'Alice' }, topicTitles: {} },
          expanded: true,
          viewer: 'bob',
        },
        expectSelector: '.ask-fold',
      },
      {
        name: '已经有人答了',
        note: '状态那一格换成是谁答的、答了什么；题目还在一行里。',
        props: {
          blocks: [askBlock([{ kind: 'option', option: '按部门', note: null, by: 'alice', at: null }])],
          asker: AGENT_NAME,
          names: { alice: 'Alice' },
          refs: { mentionNames: { alice: 'Alice' }, topicTitles: {} },
          expanded: false,
          viewer: 'bob',
        },
        expect: 'Alice：按部门',
      },
      {
        name: '提问方把题收回去了',
        note: '不再问谁、也不再说等没等到——它已经不等人答了。那一格写「芝士撤回了」，题目照旧在一行里，点开看得到原样。',
        props: {
          blocks: [askBlock([{ kind: 'withdrawn', option: null, note: null, by: 'agent', at: null }])],
          asker: AGENT_NAME,
          names: { alice: 'Alice' },
          refs: { mentionNames: { alice: 'Alice' }, topicTitles: {} },
          expanded: false,
          viewer: 'bob',
        },
        expect: '撤回',
      },
    ],
  },

  {
    id: 'ask-quick-replies',
    title: 'AskQuickReplies',
    about:
      '芝士问的一道题，画成一张卡：页头一句谁问谁（平文，不是 @ 的 chip），题目是卡的标题；选项是卡里带编号的几行，点一个（或卡有焦点时按它的数字键）就是把那几个字作为对这道题的回复发出去，和在输入框里打字是同一件事；有人答过之后换成「谁说了什么」——答案不带撤回，答了就是答了。输入框始终在，不被它占用。卡里最后一行一直是个输入框（「其他回答」）：不用先点开，打字回车就是他对这道题的答案，发的是他自己打的字。',
    file: 'src/components/ask/AskQuickReplies.vue',
    component: AskQuickReplies,
    needs: UI_T,
    states: [
      {
        name: '刚问出来',
        note: '选项各自带一句说明；模型标了推荐的那一项在选项后面跟一个「推荐」小标签。最后一行是一直摆着的输入框，和选项同一种行——模型没给的答案直接在这儿打，回车或右边的箭头发出去，发的是他自己那句话；Shift+回车换行。跳过的 ✕ 在页头右上角。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([]),
          names: {},
          viewer: 'alice',
        },
      },
      {
        name: '选项只是几个词',
        note: '没有说明的短选项也是一项一行，只是每行矮一些；末行的输入框照样在。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([], [{ text: '按部门' }, { text: '按项目' }, { text: '按季度' }]),
          names: {},
          viewer: 'alice',
        },
      },
      {
        name: '问的是别人',
        note: '题问的是 alice，看的人不在被问的人里：选项和末行的输入框都灰着、点不动，也没有跳过的 ✕；页头那句「芝士 问 Alice」说明在等谁。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([]),
          names: { alice: 'Alice' },
          viewer: 'bob',
        },
      },
      {
        name: '有人答过了',
        note: '按钮收起，列出答过的每一句：点的选项，或他自己打的字。答案就是答案，谁答的旁边都没有撤回的入口。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([
            { kind: 'option', option: '按部门', note: null, by: 'alice', at: null },
            { kind: 'note', option: null, note: '外包单列一栏', by: 'bob', at: null },
          ]),
          names: { alice: 'Alice', bob: 'Bob' },
          viewer: 'me',
        },
      },
      {
        name: '自己答过了，答案收不回来',
        note: '答题那一行就是自己答的：旁边没有撤回的入口——回答一旦发出去就算数，答错了到频道里说一声。和「有人答过了」摆在一起看，就是这一条改动。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([{ kind: 'option', option: '按部门', note: null, by: 'alice', at: null }]),
          names: { alice: 'Alice' },
          viewer: 'alice',
        },
        expect: '按部门',
      },
      {
        name: '自己跳过了，能收回来',
        note: '跳过的题不再摆选项，题上写着「你：已跳过」，旁边有一颗「撤销」：点它题重新等你回答，旁边没有确认框。答过的题没有这一颗——回答撤不回来。',
        props: {
          asker: AGENT_NAME,
          block: askBlock([{ kind: 'skipped', option: null, note: null, by: 'alice', at: null }]),
          names: { alice: 'Alice' },
          viewer: 'alice',
        },
        expect: '撤销',
      },
    ],
  },

  {
    id: 'ask-group-card',
    title: 'AskGroupCard',
    about:
      '一次问了 2–3 道题时的那张卡：顶上一栏题签，一题一屏，末屏确认，整组一次交。每题右上角有一个 ✕：那一道我不答（他自己那一条），同组别的题照旧等他答；跳过的题签上打一横、那一屏上说明一句。有人答过的题不再摆选项，答案旁边没有撤回——答了就是答了。',
    file: 'src/components/ask/AskGroupCard.vue',
    component: AskGroupCard,
    needs: UI_T,
    states: [
      {
        name: '刚问出来，第一道',
        note: '题签上是题号（有标题的写标题），右上角一个 ✕ 拒答这一道，题目下面摆选项。点「2」切下一道；「提交」在题签栏右端，不跟着一起滚。',
        props: {
          blocks: GROUP_QUESTIONS,
          names: {},
          viewer: 'alice',
        },
        expectSelector: '.ask-group__skip',
      },
      {
        name: '答过的题：答案就是答案',
        note: '有人答过的题不再摆选项，那一屏上是「谁答了什么」，右上角也没有 ✕ —— 题已经了结了。同组还没答的题照旧各自等各自的回答。',
        props: {
          blocks: groupQuestions([
            { kind: 'option', option: '按部门 (Recommended)', note: null, by: 'alice', at: null },
          ]),
          names: { alice: 'Alice' },
          viewer: 'alice',
        },
        expect: '按部门',
      },
    ],
  },
]
