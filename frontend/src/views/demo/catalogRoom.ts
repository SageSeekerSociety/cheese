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
 * 这里没有重复登记整张输入区：`components/` 下的东西（`panels/` 以外）本来就不是
 * 场景，`scene-ratchet.py` 不看它，预览站里也没有它的条目。登记的是拆出来的那几件。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ROOM_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { MentionItem } from '@/composables/useRoomMentionPicker'
import type { PendingAttachment } from '@/lib/attachments'
import type { TaskLine } from '@/lib/channelTasks'
import type { MemberCandidate } from '@/lib/memberPicker'
import type { CatalogEntry, CatalogNeed } from './catalog'

import AskQuickReplies from '../../components/ask/AskQuickReplies.vue'

import { AGENT_NAME } from './catalogFixtures'

import GuideBubble from '@/components/common/GuideBubble.vue'
import AttachmentChip from '@/components/room/AttachmentChip.vue'
import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import MemberPicker from '@/components/room/MemberPicker.vue'
import MentionMenu from '@/components/room/MentionMenu.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import TaskCreatedPost from '@/components/room/TaskCreatedPost.vue'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskSourceLine from '@/components/task/TaskSourceLine.vue'

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

// ---- 任务卡（TaskCard / TaskCreatedPost）--------------------------------------

/** 频道里的一件任务，画成卡片时用到的那几样。 */
function taskLine(over: Partial<TaskLine>): TaskLine {
  return {
    id: 'task-week-1',
    title: '整理第一周的课件',
    owner: 'cheese',
    creator: 'bob',
    status: '进行中',
    tone: 'running',
    accepted: null,
    at: new Date(Date.now() - 5 * 60_000).toISOString(),
    ...over,
  }
}

/** 上面那张卡的负责人：`taskLine` 里写的那个 handle，名字照这一册的叫法。 */
const taskOwner = { handle: 'cheese', name: AGENT_NAME, agent: true }

/** 任务页页头那一格：一件还没开始的任务，一位 AI 队友，它的会话在标准沙箱里。 */
const TASK_HEADER_PROPS = {
  room: { id: 'demo-room', project_id: 'demo', title: '前端', kind: 'topic', status: 'active' },
  task: {
    id: 'demo-task',
    project_id: 'demo',
    room_id: 'demo-room',
    title: '登录页加「记住我」',
    title_source: 'human',
    status: 'open',
    owner_handle: 'alice',
    started_at: null,
    created_at: '2026-10-05T00:00:00Z',
    updated_at: '2026-10-05T00:00:00Z',
    presentation: { column: 'not_started', phrase: 'discussing' },
  },
  memberNames: { 'cheese-topica': AGENT_NAME },
  agentName: AGENT_NAME,
  people: [{ member_handle: 'alice', agent: false }],
  agents: [{ member_handle: 'cheese-topica', agent: true }],
  machine: {
    default: { kind: 'sandbox', spec: 'standard' },
    default_from: 'room',
    devices: [],
    specs: [{ spec: 'standard', label: '标准' }],
    sessions: [
      {
        session_id: 'demo-session',
        agent_handle: 'cheese-topica',
        seat_handle: 'cheese-topica',
        environment: { kind: 'sandbox', spec: 'standard' },
        status: 'ready',
        online: true,
      },
    ],
  },
  machineError: false,
  starting: false,
  startError: null,
  actionError: null,
  start: async () => {},
  close: async () => true,
  reopen: async () => true,
  handOver: async () => true,
  rename: async () => true,
  setCollaborators: async () => true,
  setAgent: async () => true,
  loadMachine: async () => {},
  setMachine: async () => ({}),
  setMachineDefault: async () => ({}),
}

const TASK_ENTRIES: CatalogEntry[] = [
  {
    id: 'task-header',
    title: 'TaskHeader',
    about:
      '任务页的页头：「# 频道 / 任务名」和此刻的状态，右边是负责人、协作者和「开始」；点开任务信息能看到每条会话的环境。',
    file: 'src/components/task/TaskHeader.vue',
    component: TaskHeader,
    needs: UI_T,
    states: [
      {
        name: '还没开始',
        note: '一件刚建好的任务：标题在左，状态写着在讨论；数据都从 props 来，开始、改环境这些动作交给上面。',
        props: TASK_HEADER_PROPS,
        expect: '登录页加「记住我」',
      },
    ],
  },
  {
    id: 'task-source-line',
    title: 'TaskSourceLine',
    about: '任务页「概览」顶上那一行：这个任务是从哪句话来的。点它回到说这句话的那段对话，停在这一条上。',
    file: 'src/components/task/TaskSourceLine.vue',
    component: TaskSourceLine,
    needs: UI_T,
    args: {
      task: { ...TASK_HEADER_PROPS.task, created_by: 'alice' },
      memberNames: { alice: 'Alice', 'cheese-topica': AGENT_NAME },
    },
    states: [
      {
        name: '从支线里的一句话来',
        note: '引原话：谁说的、在哪个频道的支线里、什么时候；@ 过的人读成名字。',
        props: {
          source: {
            block_id: 'demo-block',
            author: 'alice',
            content: '<@cheese-topica> 登录页加一个「记住我」，勾上以后七天内不用再登录',
            created_at: '2026-10-05T00:00:00Z',
            conversation_id: 'demo-thread',
            room_id: 'demo-room',
            where: 'thread',
            title: '前端',
          },
        },
        expect: '来自 #前端 的一条支线',
      },
      {
        name: '自己新建的',
        note: '不是从哪句话转来的任务，照实说是谁新建的，不拿别的话顶替。',
        props: { source: null },
        expect: 'Alice 新建的任务，不是从对话里转出来的',
      },
    ],
  },
  {
    id: 'room-task-card',
    title: 'TaskCard',
    about: '频道里的一件任务：标题、谁负责、现在到哪一步，点一下打开这件任务。',
    file: 'src/components/room/TaskCard.vue',
    component: TaskCard,
    needs: UI_T,
    states: [
      {
        name: '进行中',
        note: '状态那一枚跟着任务走：在跑的是一个点，做完是一个勾，关掉是一道横。负责人是一张脸 + 他的名字（`owner`），不是一行字符串。',
        props: { task: taskLine({}), owner: taskOwner },
        expect: '整理第一周的课件',
      },
      {
        name: '采纳过几步',
        note: '还开着、已经采纳过的任务在状态前面写采纳了几次。',
        props: {
          task: taskLine({ accepted: '已采纳 2 次', status: '待 波比 审阅', tone: 'waiting' }),
          owner: taskOwner,
        },
        expect: '已采纳 2 次',
      },
    ],
  },
  {
    id: 'room-task-created-post',
    title: 'TaskCreatedPost',
    about: '主线上「谁新建了任务」那一条：署新建它的人，下面是那件任务的卡片。',
    file: 'src/components/room/TaskCreatedPost.vue',
    component: TaskCreatedPost,
    needs: UI_T,
    states: [
      {
        name: '新建了一件',
        note: '和一条消息一样署名、写时间，后面一句「新建了任务」。',
        props: {
          task: taskLine({}),
          creator: 'bob',
          creatorName: '波比',
          owner: taskOwner,
          avatar: null,
          time: '10:24',
        },
        expect: '新建了任务',
      },
    ],
  },
]

// ---- 待发的一枚附件（AttachmentChip）------------------------------------------

/** 待发条上的三枚附件：上传中（带进度）、传失败（留着重试）、一张图。图片那一枚走
 *  外壳注入的附件来源（`lib/attachmentSource.ts`）：目录站不注入，取不到字节，按
 *  「读不到」画 —— 这正是它真会走的一格。 */
const CHIP_UPLOADING: PendingAttachment = {
  path: 'uploading:1:big.bin',
  mime: 'application/octet-stream',
  name: 'big.bin',
  uploading: true,
  progress: 0.42,
}
const CHIP_FAILED: PendingAttachment = {
  path: 'uploading:2:notes.txt',
  mime: 'text/plain',
  name: 'notes.txt',
  error: true,
}
const CHIP_IMAGE: PendingAttachment = { path: 'uploads/demo/板书.png', mime: 'image/png', name: '板书.png' }

// ---- 挑人（MemberPicker）------------------------------------------------------

/** 「添加」那张单子上的候选：一个有头像的人、一个被邀请进来的外部成员（没头像、
 *  挂「外部」），和一位 AI 队友（画超椭圆、名字下面写它自己的 handle）。 */
const PICKER_CANDIDATES: MemberCandidate[] = [
  { handle: 'alice', name: 'Alice', avatarId: 12, agent: false },
  { handle: 'bobby', name: '波比', avatarId: null, agent: false, external: true },
  { handle: 'cheese-t1', name: '芝士', avatarId: null, agent: true, sub: 'cheese' },
]

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
      '芝士问的一道题下面那排快捷回复：点一个就是把那几个字作为对这道题的回复发出去，和在输入框里打字是同一件事；有人答过之后换成「谁说了什么」，答的那位旁边多一个「撤回」。输入框始终在，不被它占用。选项下面最后一行一直是个输入框（「其他回答」）：不用先点开，打字回车就是他对这道题的答案，发的是他自己打的字。',
    file: 'src/components/ask/AskQuickReplies.vue',
    component: AskQuickReplies,
    needs: UI_T,
    states: [
      {
        name: '刚问出来',
        note: '选项各自带一句说明；模型标了推荐的那一项多一个「推荐」。最后一行是一直摆着的输入框，和选项同一种卡——模型没给的答案直接在这儿打，回车或右边的箭头发出去，发的是他自己那句话；Shift+回车换行。',
        props: {
          block: askBlock([]),
          names: {},
          viewer: 'alice',
        },
      },
      {
        name: '选项只是几个词',
        note: '没有说明的短选项横着排成一行按钮；末行的输入框照样在，自己另起一整行。',
        props: {
          block: askBlock([], [{ text: '按部门' }, { text: '按项目' }, { text: '按季度' }]),
          names: {},
          viewer: 'alice',
        },
      },
      {
        name: '问的是别人',
        note: '题问的是 @alice，看的人不在被问的人里：选项和末行的输入框都灰着、点不动，也没有跳过的 ✕；上面那行「问 @alice」说明在等谁。',
        props: {
          block: askBlock([]),
          names: { alice: 'Alice' },
          viewer: 'bob',
        },
      },
      {
        name: '有人答过了',
        note: '按钮收起，列出答过的每一句：点的选项，或他自己打的字。答的人不是看着这一页的人，所以没有撤回的入口。',
        props: {
          block: askBlock([
            { kind: 'option', option: '按部门', note: null, by: 'alice', at: null },
            { kind: 'note', option: null, note: '外包单列一栏', by: 'bob', at: null },
          ]),
          names: { alice: 'Alice', bob: 'Bob' },
          viewer: 'me',
        },
      },
      {
        name: '自己答的，可以撤回',
        note: '答过的那一条旁边多一个「撤回」；点它先弹确认框，题回到重新等回答。',
        props: {
          block: askBlock([{ kind: 'option', option: '按部门', note: null, by: 'alice', at: null }]),
          names: { alice: 'Alice' },
          viewer: 'alice',
        },
        expect: '撤回',
      },
    ],
  },
  {
    id: 'room-attachment-chip',
    title: 'AttachmentChip',
    about:
      '输入框里待发的一个附件：左边一个记号说它是什么（缩略图 / 进度环 / 警示），右边一直写着文件名 —— 名字必须一直在，否则上传中那一格只是一个圈。',
    file: 'src/components/room/AttachmentChip.vue',
    component: AttachmentChip,
    // 记号（`v-icon` / `v-progress-circular`）、标签上的 `v-tooltip`、缩略图里的
    // `v-icon` 都是 Vuetify；名字和重试那颗的读屏标签走全局的 `t()`。
    needs: ['vuetify'],
    states: [
      {
        name: '上传中',
        note: '服务器已经报过总量，所以画的是确定的圈，报得出走了多少；一进来名字就在右边那一格，记号换掉时标签不跳。',
        props: { topicId: 'demo', attachment: CHIP_UPLOADING },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '上传失败',
        note: '失败那一枚留在待发条里：记号换成警示、多一颗「重试」，File 还在手里，按重试就是把同一份再传一次。',
        props: { topicId: 'demo', attachment: CHIP_FAILED },
        expectSelector: 'button.chip__retry',
      },
      {
        name: '带缩略图的图片',
        note: '一张图会走到缩略图那一支，但图源从外壳注入的 `AttachmentSource` 来：目录站不注入，取不到字节就画一个裂图记号 —— 这一格看的正是「读不到」的样子。',
        props: { topicId: 'demo', attachment: CHIP_IMAGE },
        expectSelector: '.im-thumb__failed',
      },
    ],
  },
  {
    id: 'room-guide-bubble',
    title: 'GuideBubble',
    about:
      '界面导览的一站：目标控件四周一圈高亮，旁边浮着一个小窗，写这里是什么、怎么用。导览里带上一步 / 下一步和第几站；「第一次」提示只有一颗「知道了」。',
    file: 'src/components/common/GuideBubble.vue',
    component: GuideBubble,
    needs: ['vuetify'],
    teleport: true,
    states: [
      {
        name: '导览的一站',
        note: '第 1 站，圈着左上角一个固定的框（预览站里没有真页面可指）：没有「上一步」。',
        props: {
          target: new DOMRect(16, 16, 160, 32),
          title: '频道',
          text: '项目里的对话按频道分开。',
          step: 1,
          total: 5,
        },
        expect: '1/5',
      },
      {
        name: '「第一次」提示',
        note: '不在导览里，只有一颗「知道了」。',
        props: { target: new DOMRect(16, 16, 160, 32), title: '任务', text: '芝士把你交给它的事做成了任务。' },
        expect: '芝士把你交给它的事做成了任务。',
      },
    ],
  },
  {
    id: 'room-member-picker',
    title: 'MemberPicker',
    about:
      '往频道里加人那一步的「挑人」：可搜索、可多选，勾好的人挂在上面当可移除的头像 chip。纯 props —— 候选名单、勾了谁、忙不忙都由调用方给，这一件一个请求都不发。',
    file: 'src/components/room/MemberPicker.vue',
    component: MemberPicker,
    // 搜索框是 `v-text-field`、勾选是 `v-icon`，都走 Vuetify；提示语、`×` 的读屏
    // 名字走全局的 `t()`。
    needs: UI_T,
    states: [
      {
        name: '一个人都还没勾',
        note: '候选一行行和名册里那一行同一个样子：头像、名字、@handle、标。勾之前一个请求都不发。',
        props: { options: PICKER_CANDIDATES, modelValue: [] },
        expectSelector: '.member-picker__row',
      },
      {
        name: '勾了两个人',
        note: '勾好的挂在上面当 chip，每一颗右边一个「×」把人拿掉；同时选中的那几行铺一条中性的底，不拿主题色去装饰选项。',
        props: { options: PICKER_CANDIDATES, modelValue: ['alice', 'cheese-t1'] },
        expectSelector: '.member-picker__chip',
      },
      {
        name: '忙着：整个不可改',
        note: '正在发请求时整件停用：搜索框、候选行都点不动，免得在路上去改选中。',
        props: { options: PICKER_CANDIDATES, modelValue: ['alice'], disabled: true },
        expectSelector: '.member-picker',
      },
    ],
  },
  ...TASK_ENTRIES,
]
