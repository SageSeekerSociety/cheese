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
import type { CatalogEntry, CatalogNeed } from './catalog'

import AskTakeoverDemo from './AskTakeoverDemo.vue'
import { AGENT_NAME } from './catalogFixtures'

import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import MentionMenu from '@/components/room/MentionMenu.vue'
import OutsideMentionNotice from '@/components/room/OutsideMentionNotice.vue'

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

/** 项目里的人，但不在这个话题里：@ 得到，候选上说一句他不在。 */
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
  { label: '所有人', kind: 'broadcast', insert: 'all', sub: '@all · 通知话题全体成员', agent: false },
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
        name: '有人不在这个话题里',
        note: '项目里的人都 @ 得到，但话题里的人排在前面；不在话题里的跟在后面，右边挂「不在话题中」——他读不到这段对话。',
        props: {
          open: true,
          matches: [PEOPLE[0], ...BROADCAST, PEOPLE[1], OUTSIDER],
          activeIndex: 4,
          level: 'root',
          enterSends: true,
        },
        expect: '不在话题中',
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
    id: 'room-outside-mention-notice',
    title: 'OutsideMentionNotice',
    about: '刚发出去的那条 @ 了不在话题里的人：说一句他们收不到通知，能管名册的人顺手拉进来。',
    file: 'src/components/room/OutsideMentionNotice.vue',
    component: OutsideMentionNotice,
    needs: UI_T,
    states: [
      {
        name: '能管名册的人',
        note: '话题的 owner / admin 看到「拉进话题」：走的是名册抽屉「添加成员」那一条接口，加完 @ 候选立刻跟上。',
        props: { names: '陈卡、波比', canAdd: true, busy: false, error: '' },
        expect: '拉进话题',
      },
      {
        name: '普通成员',
        note: '只有那句话，没有按钮——按下去后端也会拒。',
        props: { names: '陈卡', canAdd: false, busy: false, error: '' },
        expect: '不在话题中',
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
    id: 'ask-takeover',
    title: 'AskTakeoverDemo',
    about: '提问接管输入框：有题要答时输入框那一格画的是提问面板，答完它自己回来；Esc 收起后靠一条提示收回。',
    file: 'src/views/demo/AskTakeoverDemo.vue',
    component: AskTakeoverDemo,
    needs: UI_T,
    states: [
      {
        name: '一组两题，正在接管',
        note: '按真实聊天栏的尺寸摆：上面是对话区、下面是输入那一格。这一格里的「输入框」其实是提问面板。',
        props: { questions: 2 },
      },
      {
        name: '只剩一题',
        note: '一题时不画 i of N 的前后按钮。',
        props: { questions: 1 },
      },
      {
        name: '收起之后',
        note: 'Esc 只收起当前这一组，输入框上方出现「有 N 个问题待回答」，点它把面板叫回来。',
        props: { questions: 2, dismissed: true },
      },
    ],
  },
]
