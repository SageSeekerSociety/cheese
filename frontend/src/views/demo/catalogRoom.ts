/**
 * 输入区（`RoomComposer`）拆出来的那几件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，这几条塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogChat.ts`、`catalogAccept.ts` 同一个
 * 理由。
 *
 * 为什么这些件值得一站：拆之前它们都是 1039 行 `RoomComposer.vue` 里的几段模板
 * （#2143），想看其中任何一段都得先把整个输入区拉起来 —— 而输入区要话题、要名册、
 * 要 socket。拆开之后每一件都只吃 props、只往上发事件，于是能单独摆在预览站里。
 *
 * 末尾另收主线上的任务卡那两件（`TaskCard`、`TaskCreatedPost`，数据见
 * `catalogRoomFixtures.ts`）：同样是房间里的行，同样只吃 props、点了往外 emit。
 *
 * 这里没有重复登记整张输入区：`components/` 下的东西（`panels/` 以外）本来就不是
 * 场景，`scene-ratchet.py` 不看它，预览站里也没有它的条目。登记的是拆出来的那几件。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ROOM_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { MentionItem } from '@/composables/useRoomMentionPicker'
import type { CatalogEntry, CatalogNeed } from './catalog'

import AskQuickReplies from '../../components/ask/AskQuickReplies.vue'

import { AGENT_NAME } from './catalogFixtures'
import {
  TASK_BY_AGENT,
  TASK_CLOSED,
  TASK_DONE,
  TASK_MINE,
  TASK_RUNNING,
  TASK_STUCK,
  TASK_WAITING,
  taskPostProps,
} from './catalogRoomFixtures'

import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import MentionMenu from '@/components/room/MentionMenu.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import TaskCreatedPost from '@/components/room/TaskCreatedPost.vue'

/** 这几件都要 vuetify（`v-icon` / `v-spacer` / `v-btn`），还都有不写死在模板里的字：
 *  「外部」、「取消回复」、`t('work.room.composer.summon')`。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

// ---- @ 候选菜单（MentionMenu）------------------------------------------------

/** 房间里的人。名册上那位 AI 队友（`agent`）在候选里排最前，被邀请进来的外部
 *  成员挂「外部」——两样都是候选行的一部分，不是装饰。 */
const PEOPLE: MentionItem[] = [
  {
    label: AGENT_NAME,
    kind: 'member',
    insert: AGENT_NAME,
    sub: '@cheese-topica',
    agent: true,
    handle: 'cheese-topica',
  },
  { label: 'Alice', kind: 'member', insert: 'Alice', sub: '@alice', agent: false, handle: 'alice' },
  { label: '波比', kind: 'member', insert: '波比', sub: '@bobby', agent: false, external: true, handle: 'bobby' },
]

/** 项目里的人，但没加入这个频道：@ 得到，排在频道里的人后面。 */
const OUTSIDER: MentionItem = {
  label: '陈卡',
  kind: 'member',
  insert: '陈卡',
  sub: '@carol',
  agent: false,
  outsideTopic: true,
  handle: 'carol',
}

/** 群播：两个 fixed-literal token，`expandMentions` 把它们变成 `<@all>` / `<@here>`。 */
const BROADCAST: MentionItem[] = [
  { label: '所有人', kind: 'broadcast', insert: 'all', sub: '@all · 通知频道全体成员', agent: false },
  { label: '在线成员', kind: 'broadcast', insert: 'here', sub: '@here · 通知在线成员', agent: false },
]

/** 没打字时资料库那一行入口。它不是一个人，走进去了才列文件。 */
const LIBRARY_ENTRY: MentionItem = {
  label: '资料库',
  kind: 'category',
  insert: 'library',
  sub: '12 份文件',
  agent: false,
}

/** 二级菜单里的文件。同一组的标题只画一次，所以只有第一条带 `group`。 */
const FILES: MentionItem[] = [
  { label: 'docs/课程大纲.md', kind: 'file', insert: 'docs/课程大纲.md', sub: '文件', agent: false, group: '文件' },
  { label: 'docs/评分标准.pdf', kind: 'file', insert: 'docs/评分标准.pdf', sub: '文件', agent: false, group: '文件' },
  { label: 'img/板书.png', kind: 'file', insert: 'img/板书.png', sub: '图片', agent: false, group: '图片' },
]

// ---- 待发的那一行（ComposerChipRow）------------------------------------------

/** 待发的附件。两种都不是「有第一页可画」的类型，所以预览站不必有后端：方格
 *  里摆的是它们各自的类型图标。 */
const CHIP_ATTS = [
  { path: 'uploads/demo/预算.xlsx', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
  { path: 'uploads/demo/课程大纲.md', mime: 'text/markdown' },
]

// ---- 动作行（ComposerActions）------------------------------------------------

/** 那一行除了按钮之外什么都不带（算力 chips 由话题自己从插槽交进来）。 */
const ACTIONS_BASE = {
  uploading: false,
  canSend: false,
  showImagePicker: false,
  enterSends: true,
  alwaysSummon: false,
  summonOn: false,
  summonReady: true,
  agentName: AGENT_NAME,
}

/** 芝士问的一道题，`answer_log` 由各格给。 */
function askBlock(answerLog: Record<string, unknown>[]) {
  return {
    id: 'demo-question',
    topic_id: 'demo-room',
    kind: 'message',
    author: 'cheese',
    author_type: 'participant',
    content: '预算按哪个口径统计？',
    created_at: '2026-10-05T09:00:00Z',
    meta: {
      options: [
        { text: '按部门 (Recommended)', explain: '和去年的报表对得上' },
        { text: '按项目', explain: '每个项目一张表' },
      ],
      asked: 'alice',
      answer_log: answerLog,
    },
  }
}

export const ROOM_ENTRIES: CatalogEntry[] = [
  {
    id: 'room-mention-menu',
    title: 'MentionMenu',
    about: '@ 候选菜单：一级是「人 / 群播 / 资料库」，进资料库才列文件。',
    file: 'src/components/room/MentionMenu.vue',
    component: MentionMenu,
    needs: UI_T,
    states: [
      {
        name: '刚打一个 @',
        note: '第一格是 AI 队友，资料库夹在它和群播之间，被邀请进来的人挂「外部」。菜单浮在输入框上方（`position: absolute`），预览站里没有那个盒子，所以它贴在这一格的右上角。',
        props: {
          open: true,
          matches: [PEOPLE[0], LIBRARY_ENTRY, ...BROADCAST, PEOPLE[1], PEOPLE[2]],
          activeIndex: 0,
          level: 'root',
          enterSends: true,
        },
        expect: '资料库',
      },
      {
        name: '有人没加入这个频道',
        note: '项目里的人都 @ 得到，但频道里的人排在前面；没加入的跟在后面。',
        props: {
          open: true,
          matches: [PEOPLE[0], ...BROADCAST, PEOPLE[1], OUTSIDER],
          activeIndex: 4,
          level: 'root',
          enterSends: true,
        },
        expect: '陈卡',
      },
      {
        name: '高亮移到别人身上',
        note: '高亮只有一套：鼠标划过和 ↑/↓ 改的是同一个下标，所以「Enter」那个提示永远长在真的会被挑中的那一行上。',
        props: { open: true, matches: PEOPLE, activeIndex: 2, level: 'root', enterSends: true },
        expect: '波比',
      },
      {
        name: '打了字：人、话题、文件一起搜',
        note: '打了字就不分级了——这时候人要的是搜索，所以文件直接和人并列，资料库那一行入口让位。',
        props: {
          open: true,
          matches: [PEOPLE[1], FILES[0], FILES[2], LIBRARY_ENTRY],
          activeIndex: 0,
          level: 'root',
          enterSends: true,
        },
        expect: 'docs/课程大纲.md',
      },
      {
        name: '进了资料库这一层',
        note: '二级菜单的头就是「‹ 资料库」那颗退回按钮（键盘上是 Esc、←，或 @ 后面没打字时的退格），文件按「文件 / 图片」分组，每组标题只画一次。',
        props: { open: true, matches: FILES, activeIndex: 0, level: 'library', enterSends: true },
        expect: '图片',
      },
      {
        name: '一级一份都没匹配上',
        note: '菜单不消失：说一句「暂无匹配」比整块收起来诚实——不然看起来像那个 @ 没生效。空态顶替的是一列候选，所以内边距和字号跟着候选行走。',
        props: { open: true, matches: [], activeIndex: 0, level: 'root', enterSends: false },
        expect: '暂无匹配',
      },
      {
        name: '资料库里一份都没匹配上',
        note: '菜单不消失，说的是文件那一件事：「暂无匹配的文件」。空态和一级上那个同一副骨架。',
        props: { open: true, matches: [], activeIndex: 0, level: 'library', enterSends: false },
        expect: '暂无匹配的文件',
      },
    ],
  },
  {
    id: 'room-composer-chip-row',
    title: 'ComposerChipRow',
    about: '这条消息还带着什么：回复的那条在最前，后面是待发的附件，一枚一个标签。',
    file: 'src/components/room/ComposerChipRow.vue',
    component: ComposerChipRow,
    needs: UI_T,
    states: [
      {
        name: '回复一条，还带着两个附件',
        note: '一行排开、不换行，多了就在这一行里横着滚。回复那一枚字淡一档：它说的是上下文，不是这条消息带着的东西。',
        props: { replyLabel: '回复 波比：你看下这个', atts: CHIP_ATTS, topicId: 'demo' },
        expect: '回复 波比：你看下这个',
      },
      {
        name: '只有附件',
        note: '附件那一枚左边是它的记号（图片是缩略图、有第一页的文档是封面、其余是类型图标），右边一直写着名字。',
        props: { replyLabel: null, atts: CHIP_ATTS.slice(0, 1), topicId: 'demo' },
        expect: '预算.xlsx',
      },
    ],
  },
  {
    id: 'room-composer-actions',
    title: 'ComposerActions',
    about: '输入框下面那一行：左边是「这条消息本身」的动作，右边是「它会怎么发出去」。',
    file: 'src/components/room/ComposerActions.vue',
    component: ComposerActions,
    needs: UI_T,
    states: [
      {
        name: '什么都没写',
        note: '发送键是灰的（这一行唯一的实心块）。「交给芝士」开着的时候只改一条描边和墨色，形态不变——一行里只有一个实心块，那个位置是发送的。',
        props: ACTIONS_BASE,
        expect: '交给芝士',
      },
      {
        name: '这条会开着芝士跑',
        note: '正文里 @ 了它：按钮是填充的，一眼认得出这条消息会真的开出一轮，和「只是说了句话」是两回事。',
        props: { ...ACTIONS_BASE, canSend: true, summonOn: true },
        expect: '交给芝士',
      },
      {
        name: '有字了，能发',
        note: '灰键过渡成琥珀；按下去沉一下，不等松手。',
        props: { ...ACTIONS_BASE, canSend: true },
        expect: '交给芝士',
      },
      {
        name: '手机上多一颗「照片」',
        note: '那儿没有截图可贴、也没有东西可拖，从文件选择器里翻相册要绕好几步。',
        props: { ...ACTIONS_BASE, showImagePicker: true },
        expect: '交给芝士',
      },
      {
        name: '名册还没到，芝士是谁还不知道',
        note: '这一瞬间「交给芝士」是关着的：解析不出 handle，替人写进正文的那个 @ 只是一行字，消息照发、它不动。少一个入口，好过一个点了不算数的入口。',
        props: { ...ACTIONS_BASE, summonReady: false },
        expect: '交给芝士',
      },
      {
        name: '上传还没回来',
        note: '这一刻发不出去，所以发送键是灰的——而不是把字吞掉。',
        props: { ...ACTIONS_BASE, canSend: true, uploading: true },
        expect: '交给芝士',
      },
      {
        name: '私聊：没有「交给芝士」这一颗',
        note: '那儿每条都是说给它听的，所以这一格只剩发送——`alwaysSummon` 一开，按钮整颗不画。算力那一枚是话题从 `chips` 具名插槽交进来的，预览站的格子只喂默认插槽、喂不进去，少的那一枚在这一格看不见。',
        props: { ...ACTIONS_BASE, alwaysSummon: true, canSend: true },
      },
    ],
  },

  {
    id: 'ask-quick-replies',
    title: 'AskQuickReplies',
    about:
      '芝士问的一道题下面那排快捷回复：点一个就是把那几个字作为对这道题的回复发出去，和在输入框里打字是同一件事；有人答过之后换成「谁说了什么」。输入框始终在，不被它占用。',
    file: 'src/components/ask/AskQuickReplies.vue',
    component: AskQuickReplies,
    needs: UI_T,
    states: [
      {
        name: '刚问出来',
        note: '选项各自带一句说明；模型标了推荐的那一项多一个「推荐」。',
        props: {
          block: askBlock([]),
          names: {},
        },
      },
      {
        name: '有人答过了',
        note: '按钮收起，列出答过的每一句：点的选项，或他自己打的字。',
        props: {
          block: askBlock([
            { kind: 'option', option: '按部门', note: null, by: 'alice', at: null },
            { kind: 'note', option: null, note: '外包单列一栏', by: 'bob', at: null },
          ]),
          names: { alice: 'Alice', bob: 'Bob' },
        },
      },
    ],
  },
  {
    id: 'room-task-card',
    title: 'TaskCard',
    about: '频道主线上的一张任务卡：标题、谁负责、此刻到哪一步、上一次变化是什么时候。点它打开任务。',
    file: 'src/components/room/TaskCard.vue',
    component: TaskCard,
    // 状态那一格的勾和横线是 `v-icon`；「某某 负责」和相对时间都读当前语言。
    needs: UI_T,
    args: { ownerName: '王长鑫' },
    states: [
      {
        name: '运行中',
        note: '芝士在干活：状态前一颗呼吸的点。',
        props: { task: TASK_RUNNING },
        expect: '运行中',
      },
      {
        name: '在等别人',
        note: '等的是一个人时写出他的名字，而不是笼统的「待审阅」。',
        props: { task: TASK_WAITING, ownerName: '李甘' },
        expect: '待 李甘 审阅',
      },
      {
        name: '在等你，已经采纳过两步',
        note: '等的就是看的这个人：卡加一道强调边、说成「待你审阅」；还开着又采纳过的，挂「已采纳 2 次」。',
        props: { task: TASK_MINE },
        expect: '已采纳 2 次',
      },
      {
        name: '检查没过',
        note: '闸门红了：点换成警示色。',
        props: { task: TASK_STUCK },
        expect: '检查未通过',
      },
      {
        name: '做完了',
        note: '采纳合并：点换成一个勾，状态写出是哪个 PR。',
        props: { task: TASK_DONE },
        expect: '已采纳 · PR #128',
      },
      {
        name: '关了，没人负责',
        note: '没做就关的：一道横线；没有负责人时那一段不画。',
        props: { task: TASK_CLOSED, ownerName: null },
        expect: '已关闭',
      },
      {
        name: '支线区里的一行',
        note: '`inList` 时它是几行中的一行，不单独画边框，也不加「在等你」那道边。',
        props: { task: TASK_MINE, inList: true },
        expectSelector: '.task-card--row',
      },
    ],
  },
  {
    id: 'room-task-created-post',
    title: 'TaskCreatedPost',
    about: '有人在频道里单独新建了一件任务：主线上署他的名，「新建了任务」下面一张任务卡。',
    file: 'src/components/room/TaskCreatedPost.vue',
    component: TaskCreatedPost,
    needs: UI_T,
    states: [
      {
        name: '人新建的',
        note: '头像、名字、时间、「新建了任务」，下面是那张卡。',
        props: taskPostProps(TASK_RUNNING),
        expect: '新建了任务',
      },
      {
        name: '接着上一件',
        note: '同一个人接连新建的几件归在同一个名字下面（cont）：不再重复头像和署名，只剩卡。',
        props: taskPostProps(TASK_WAITING, { cont: true }),
        expectSelector: '.im-row--cont',
      },
      {
        name: 'AI 队友新建的',
        note: '新建它的是 AI 队友时，头像换成芝士的那一个。',
        props: taskPostProps(TASK_BY_AGENT),
        expect: '讨论中',
      },
      {
        name: '认不出是谁',
        note: '新建的人认不出来（creator 为 null）时不署名，只留时间和那张卡。',
        props: taskPostProps(TASK_DONE, { creator: null }),
        expect: '已采纳 · PR #128',
      },
    ],
  },
]
