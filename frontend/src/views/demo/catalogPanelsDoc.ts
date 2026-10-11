/**
 * `panels/doc/` 这一册：文档面板拆出来的那些零件 —— 顶栏、浮条、大纲、吐槽栏、
 * AI 队友那几张卡、修改记录、版本对比……一格一格摆出来。
 *
 * 单独一份，和 `catalogDoc.ts` 同一个理由：`catalog.ts` 已经顶到一千行的上限；
 * `catalogPanels.ts` 是它的把手，这里的两份数组从那里 spread 进去。样例数据在
 * `catalogPanelsDocFixtures.ts`。
 *
 * 编辑器那几件（浮条、slash 菜单、链接框）拿的是**真的** tiptap 编辑器（`demoEditor`），
 * 不是空壳：它们要的编辑器身上的状态，只有真编辑器给得出。只因为没选中字、没焦点而
 * 整件不出现的（DocEditLayer、DocKeyboardBar），这里不列，原因写在任务的覆盖名单里。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  demoEditor,
  DOC_AGENT,
  DOC_AGENT_HANDLE,
  DOC_DIFF,
  DOC_DIFF_EMPTY,
  DOC_HEADINGS,
  DOC_HEADINGS_EMPTY,
  DOC_MARKDOWN,
  DOC_MENTION_NAMES,
  DOC_PEERS,
  DOC_PERSON,
  DOC_PERSON_HANDLE,
  DOC_PRESET_DOC,
  DOC_PRESET_EDIT,
  DOC_SLASH_ITEMS,
  DOC_THREAD_ACTIONS,
  docNameOf,
  docPlaceOf,
  docSurfaceSession,
  docThreadState,
  docThreadStateEmpty,
  docVersionPage,
  docVersionPageEmpty,
} from './catalogPanelsDocFixtures'

import DocAgentBox from '@/components/panels/doc/DocAgentBox.vue'
import DocAgentResult from '@/components/panels/doc/DocAgentResult.vue'
import DocBubble from '@/components/panels/doc/DocBubble.vue'
import DocCommentPanel from '@/components/panels/doc/DocCommentPanel.vue'
import DocComments from '@/components/panels/doc/DocComments.vue'
import DocEditButton from '@/components/panels/doc/DocEditButton.vue'
import DocFindBar from '@/components/panels/doc/DocFindBar.vue'
import DocHistory from '@/components/panels/doc/DocHistory.vue'
import DocLinkCallout from '@/components/panels/doc/DocLinkCallout.vue'
import DocOutline from '@/components/panels/doc/DocOutline.vue'
import DocOverlays from '@/components/panels/doc/DocOverlays.vue'
import DocPresence from '@/components/panels/doc/DocPresence.vue'
import DocReviewCard from '@/components/panels/doc/DocReviewCard.vue'
import DocReviewStrip from '@/components/panels/doc/DocReviewStrip.vue'
import DocSlashMenu from '@/components/panels/doc/DocSlashMenu.vue'
import DocSuggestionCard from '@/components/panels/doc/DocSuggestionCard.vue'
import DocSuggestionStrip from '@/components/panels/doc/DocSuggestionStrip.vue'
import DocSurface from '@/components/panels/doc/DocSurface.vue'
import DocThreadCard from '@/components/panels/doc/DocThreadCard.vue'
import DocTopBar from '@/components/panels/doc/DocTopBar.vue'
import DocVersionDiff from '@/components/panels/doc/DocVersionDiff.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n']
const T: CatalogNeed[] = ['i18n']

/** 顶栏/正文那一层要的那一大串：多数格都一样，抽出来一份。 */
function topBarProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    loading: false,
    connection: 'connected',
    peers: [],
    editable: true,
    readOnly: false,
    lastEdit: null,
    suggestionCount: 0,
    suggestionsOpen: false,
    commentCount: 0,
    commentsOpen: false,
    agentName: DOC_AGENT,
    mentionNames: DOC_MENTION_NAMES,
    headings: DOC_HEADINGS_EMPTY,
    findOpen: false,
    ...over,
  }
}

const editorForBubble = demoEditor(DOC_MARKDOWN)
const editorForLink = demoEditor(DOC_MARKDOWN)

export const DOC_PANEL_ENTRIES: CatalogEntry[] = [
  {
    id: 'panel-doc-top-bar',
    title: 'DocTopBar · 文档顶栏',
    about:
      '文档顶栏：左边是这篇现在的状态（谁多久前编辑的、断网、只读），右边是在线的人、建议、评论、对整篇问 AI 队友和「⋯」。',
    file: 'src/components/panels/doc/DocTopBar.vue',
    component: DocTopBar,
    needs: UI,
    states: [
      {
        name: '有人编辑过',
        note: '左边写着「王长鑫 …编辑」，点它开修改记录；右边一排在线的头像。',
        props: topBarProps({ lastEdit: { name: DOC_PERSON, at: '2026-09-30T02:00:00.000Z' }, peers: DOC_PEERS }),
        expect: '编辑',
      },
      {
        name: '断网了',
        note: '左边换成「没连上，改动会在连上后同步」，不是「谁编辑的」。',
        props: topBarProps({ connection: 'offline' }),
        expect: '没连上',
      },
      {
        name: '只读',
        note: '没有编辑权限：左边一颗「只读」，不能切回可编辑。',
        props: topBarProps({ editable: false, readOnly: true }),
        expect: '只读',
      },
    ],
  },
  {
    id: 'panel-doc-outline',
    title: 'DocOutline · 大纲',
    about: '文档大纲：按级别缩进列出正文里的标题，点一节滚到那一段；还没有标题时给一句空态。',
    file: 'src/components/panels/doc/DocOutline.vue',
    component: DocOutline,
    needs: UI,
    states: [
      {
        name: '有标题',
        note: '三级标题各缩进一档，点了把 pos 发上去。',
        props: { headings: DOC_HEADINGS },
        expect: '上线过程',
      },
      {
        name: '还没标题',
        note: '正文里一个标题都没有：给一句空态，而不是一个打不开的空白框。',
        props: { headings: DOC_HEADINGS_EMPTY },
        expect: '这篇文档还没有标题',
      },
    ],
  },
  {
    id: 'panel-doc-presence',
    title: 'DocPresence · 在线的人',
    about: '顶栏上「谁也在这儿」：同一篇文档打开着的其他人的头像，人是圆的、AI 队友是方的，超过四个收成「+N」。',
    file: 'src/components/panels/doc/DocPresence.vue',
    component: DocPresence,
    needs: T,
    states: [
      {
        name: '一屋子人',
        note: '六个人，画前四个，多的那两位收成「+2」。同一个人开两个标签页只画一次。',
        props: { peers: DOC_PEERS },
        expect: '+2',
      },
      {
        name: '一个人',
        note: '只有一个人在这儿时画一张脸，没有「+N」。',
        props: { peers: [DOC_PEERS[0]] },
        expectSelector: '.doc-presence__face',
      },
    ],
  },
  {
    id: 'panel-doc-edit-button',
    title: 'DocEditButton · 文档里的小按钮',
    about: '文档里那几张小卡片和横条上用的按钮：默认、一组里唯一的主动作（深底）、不可用三种。',
    file: 'src/components/panels/doc/DocEditButton.vue',
    component: DocEditButton,
    states: [
      { name: '默认', note: '浅底描边，卡片上的次要动作。', slot: '接受', expect: '接受' },
      {
        name: '主动作',
        note: '一深底；一组里只有一个，接受和全部接受会同时在屏幕上。',
        slot: '全部接受',
        props: { strong: true },
        expect: '全部接受',
      },
      {
        name: '不可用',
        note: '忙着或没权限时按不动。',
        slot: '还原这处',
        props: { disabled: true },
        expect: '还原这处',
      },
    ],
  },
  {
    id: 'panel-doc-find-bar',
    title: 'DocFindBar · 文档内查找',
    about: '文档内查找那一条：输入框、第几个 / 共几个、上一个 / 下一个、关闭；没有匹配时说「没有找到」。',
    file: 'src/components/panels/doc/DocFindBar.vue',
    component: DocFindBar,
    needs: UI,
    states: [
      {
        name: '有匹配',
        note: '查到五处，停在第 2 处。',
        props: { open: true, query: '搜索', total: 5, current: 2 },
        expect: '2/5',
      },
      {
        name: '没有找到',
        note: '查的词一个都没匹配上。',
        props: { open: true, query: '不存在', total: 0, current: 0 },
        expect: '没有找到',
      },
    ],
  },
  {
    id: 'panel-doc-slash-menu',
    title: 'DocSlashMenu · 块类型菜单',
    about: 'Notion 式 slash 菜单的浮层：在正文里打「/」弹出，列块类型，键盘在列表里走、鼠标也可以点。',
    file: 'src/components/panels/doc/DocSlashMenu.vue',
    component: DocSlashMenu,
    needs: ['vuetify'],
    states: [
      {
        name: '一张表',
        note: '行首打「/」弹出的块类型表：图标、名字、右边一句提示；键盘停在哪一行，那一行就亮着。',
        props: { items: DOC_SLASH_ITEMS.slice(0, 6), index: 1, top: 0, left: 0 },
        expect: '标题 1',
      },
    ],
  },
  {
    id: 'panel-doc-overlays',
    title: 'DocOverlays · 压在正文上的那几块',
    about:
      '压在正文上、跟着正文走的那几块：选中文字的浮条、slash 菜单浮层、代码块工具条、块手柄。位置都自己算，因为不是正文的一部分。',
    file: 'src/components/panels/doc/DocOverlays.vue',
    component: DocOverlays,
    needs: UI,
    states: [
      {
        name: 'slash 菜单开着',
        note: '上面说「菜单开在哪」，这里只负责画：一条块类型浮层贴在光标附近。',
        props: {
          editor: null,
          editable: true,
          topicId: null,
          slashMenu: { items: DOC_SLASH_ITEMS.slice(0, 6), index: 0, top: 0, left: 0 },
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
        },
        expect: '标题 1',
      },
    ],
  },
  {
    id: 'panel-doc-bubble',
    title: 'DocBubble · 选中文字后的浮条',
    about:
      '选中文字后浮在上面的那一条：AI 队友、评论，能改时再加块样式、几种字的格式、链接；不能改时把格式换成「复制」。手机上换成键盘上方那一条。',
    file: 'src/components/panels/doc/DocBubble.vue',
    component: DocBubble,
    needs: UI,
    states: [
      {
        name: '能改',
        note: '能改：左边是「芝士」和「评论」，往后是块样式、加粗/斜体/删除线/行内代码/高亮、三种状态标签、链接。',
        props: {
          editor: editorForBubble,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: true,
          canAgent: true,
          canComment: true,
          hasSelection: true,
        },
        expect: '评论',
      },
      {
        name: '不能改',
        note: '只读时格式都收起，只留一颗「复制」；没选中字时它是灰的。',
        props: {
          editor: editorForBubble,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: false,
          canAgent: true,
          canComment: true,
          hasSelection: false,
        },
        expect: '复制',
      },
      {
        name: '状态标签里',
        note: '光标停在一个状态标签里：只给「是 / 否 / 有风险」和「去掉」，不给格式。',
        props: {
          editor: editorForBubble,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: true,
          canAgent: true,
          canComment: true,
          statusOnly: true,
        },
        expect: '去掉',
      },
      {
        name: '手机上的那一条',
        note: '手机上贴在键盘上方：没选中字时 AI 队友和评论按不了，格式对着光标处要打的字，末尾多了撤销、重做。',
        props: {
          editor: editorForBubble,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: true,
          canAgent: true,
          canComment: true,
          variant: 'bar',
          hasSelection: false,
        },
        expect: '评论',
      },
    ],
  },
  {
    id: 'panel-doc-link-callout',
    title: 'DocLinkCallout · 链接框',
    about: '给选中的字加链接、改链接、去掉链接的那个浮框。位置贴着那几个字自己算。',
    file: 'src/components/panels/doc/DocLinkCallout.vue',
    component: DocLinkCallout,
    needs: UI,
    states: [
      {
        name: '填地址',
        note: '选中的字上还没有链接：直接给输入框和「保存 / 取消」，回车就是保存。',
        props: {
          target: { editor: editorForLink, doc: editorForLink.state.doc, from: 0, to: 0, mark: null, href: '' },
        },
        expect: '保存',
      },
    ],
  },
  {
    id: 'panel-doc-version-diff',
    title: 'DocVersionDiff · 和上一版比',
    about:
      '修改记录右边那一栏：选中的一版和它上一版比，改了哪几段 —— 加的段绿底、删的段红底带删除线、改了的段在行内标出增删的字。',
    file: 'src/components/panels/doc/DocVersionDiff.vue',
    component: DocVersionDiff,
    needs: T,
    states: [
      {
        name: '改了几段',
        note: '上面一句是多少段的增删改，下面按段排。',
        props: { diff: DOC_DIFF },
        expect: '修改 1 段',
      },
      {
        name: '和上一版一样',
        note: '两版之间没有改动：只留上面那一句和一句「没有改动」。',
        props: { diff: DOC_DIFF_EMPTY },
        expect: '和上一版相比没有改动',
      },
    ],
  },
  {
    id: 'panel-doc-history',
    title: 'DocHistory · 修改记录',
    about:
      '修改记录对话框：左边每一版（新的在上），右边默认是和上一条之间改了什么，也能切到那一版当时的全文；能改时可以恢复到那一版。',
    file: 'src/components/panels/doc/DocHistory.vue',
    component: DocHistory,
    needs: UI,
    teleport: true,
    states: [
      {
        name: '有几版',
        note: '读了四版，选中最新一版，右边是它和上一版的对比。',
        props: {
          open: true,
          load: docVersionPage,
          editable: true,
          nameOf: docNameOf,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '修改记录',
      },
      {
        name: '还没有记录',
        note: '一版都没有：右边给一句空态。',
        props: {
          open: true,
          load: docVersionPageEmpty,
          editable: true,
          nameOf: docNameOf,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '暂无修改记录',
      },
    ],
  },
  {
    id: 'panel-doc-agent-result',
    title: 'DocAgentResult · AI 队友那张卡',
    about:
      '输入框交出去以后的那张卡：排着队、在改或在答（都能停）；改好了说改了几处、能撤销、能接着说；答了就是回答本身，能转成评论。',
    file: 'src/components/panels/doc/DocAgentResult.vue',
    component: DocAgentResult,
    needs: T,
    states: [
      {
        name: '排队中',
        note: '它正忙：说一句「空闲后自动回复」，只给「取消」。',
        props: {
          agentName: DOC_AGENT,
          phase: 'queued',
          kind: 'edit',
          answer: '',
          changed: 0,
          busy: true,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '空闲后自动回复',
      },
      {
        name: '正在改',
        note: '在改这篇：说「正在修改…」，给「停止」。',
        props: {
          agentName: DOC_AGENT,
          phase: 'working',
          kind: 'edit',
          answer: '',
          changed: 0,
          busy: true,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '正在修改',
      },
      {
        name: '改好了',
        note: '改好了：说改了几处，给「撤销」，下面还有「继续修改」。',
        props: {
          agentName: DOC_AGENT,
          phase: 'done',
          kind: 'edit',
          answer: '',
          changed: 3,
          busy: false,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '已修改 3 处',
      },
      {
        name: '撤销了',
        note: '撤销之后能恢复。',
        props: {
          agentName: DOC_AGENT,
          phase: 'undone',
          kind: 'edit',
          answer: '',
          changed: 0,
          busy: false,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '已撤销',
      },
      {
        name: '答了',
        note: '回答按 Markdown 读：能转成评论、能接着问。',
        props: {
          agentName: DOC_AGENT,
          phase: 'answered',
          kind: 'ask',
          answer: '用时少了一秒。',
          changed: 0,
          busy: false,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '用时少了一秒',
      },
      {
        name: '没发现问题',
        note: '问了、它答不上什么可说的：给一句「未发现问题」。',
        props: {
          agentName: DOC_AGENT,
          phase: 'answered',
          kind: 'ask',
          answer: '',
          changed: 0,
          busy: false,
          commentable: true,
          mentionNames: DOC_MENTION_NAMES,
        },
        expect: '未发现问题',
      },
    ],
  },
  {
    id: 'panel-doc-agent-box',
    title: 'DocAgentBox · 点 AI 队友开出的输入框',
    about:
      '点 AI 队友开出的输入框：上面是输入框，一打开就能打字；下面是常用的说法，分「修改」「提问」两组，打字时跟着筛。',
    file: 'src/components/panels/doc/DocAgentBox.vue',
    component: DocAgentBox,
    needs: UI,
    states: [
      {
        name: '选中一段改',
        note: '选了一段字：先是「修改」那组（润色、精简、改成列表/表格、翻译），后面跟「提问」。',
        props: { agentName: DOC_AGENT, scope: 'selection', context: DOC_PRESET_EDIT },
        expect: '润色',
      },
      {
        name: '对整篇问',
        note: '对整篇：只有「提问」那组（总结要点、检查全文、提出结构建议）。',
        props: { agentName: DOC_AGENT, scope: 'document', context: DOC_PRESET_DOC },
        expect: '总结要点',
      },
      {
        name: '只读只能问',
        note: '不能改时只有「提问」：输入框的提示语换成「输入问题」。',
        props: { agentName: DOC_AGENT, scope: 'selection', context: { editable: false, list: false, chinese: true } },
        expectSelector: 'input[aria-label="输入问题"]',
      },
    ],
  },
  {
    id: 'panel-doc-suggestion-strip',
    title: 'DocSuggestionStrip · 建议那条',
    about:
      '正文上方那一条：AI 队友提的修改建议还有几处，上一处 / 下一处，一次全部决定；都处理完了说一声各接受、拒绝了几处。',
    file: 'src/components/panels/doc/DocSuggestionStrip.vue',
    component: DocSuggestionStrip,
    needs: UI,
    states: [
      {
        name: '还有几处',
        note: '还有三处没处理：上一处 / 下一处，加「全部拒绝 / 全部接受」。',
        props: { agentName: DOC_AGENT, count: 3, decided: { accepted: 0, rejected: 0 }, editable: true },
        expect: '还有 3 处待处理',
      },
      {
        name: '都处理完了',
        note: '处理完了：说一声接受两处、拒绝一处，只留一个关闭。',
        props: { agentName: DOC_AGENT, count: 0, decided: { accepted: 2, rejected: 1 }, editable: true },
        expect: '接受 2 处，拒绝 1 处',
      },
    ],
  },
  {
    id: 'panel-doc-suggestion-card',
    title: 'DocSuggestionCard · 一处建议',
    about: '看着的那一处建议下面的卡：谁提的、第几处、为什么，接受或拒绝。',
    file: 'src/components/panels/doc/DocSuggestionCard.vue',
    component: DocSuggestionCard,
    needs: T,
    states: [
      {
        name: '带理由',
        note: '它说了为什么改：卡上写出来，下面是「拒绝 / 接受」。',
        props: {
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          index: 0,
          total: 2,
          reason: '把「翻倍」改成「翻了三倍」更准。',
          editable: true,
        },
        expect: '把「翻倍」改成「翻了三倍」更准。',
      },
      {
        name: '没理由',
        note: '没说为什么：只写谁提的、第几处。',
        props: {
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          index: 1,
          total: 2,
          reason: null,
          editable: true,
        },
        expect: '芝士的建议',
      },
    ],
  },
  {
    id: 'panel-doc-review-strip',
    title: 'DocReviewStrip · 改动那条',
    about: '「查看改动」打开文档时正文上方那一条：谁让 AI 队友改的、还有几处在，上一处 / 下一处、收起。',
    file: 'src/components/panels/doc/DocReviewStrip.vue',
    component: DocReviewStrip,
    needs: UI,
    states: [
      {
        name: '还有几处',
        note: '「王长鑫让芝士改的 3 处」，给上一处 / 下一处和收起。',
        props: { agentName: DOC_AGENT, requester: DOC_PERSON, count: 3 },
        expect: '王长鑫让芝士改的 3 处',
      },
      {
        name: '都还原了',
        note: '几处都还原了：换成一句「都已还原」，收成只剩收起。',
        props: { agentName: DOC_AGENT, requester: DOC_PERSON, count: 0 },
        expect: '都已还原',
      },
    ],
  },
  {
    id: 'panel-doc-review-card',
    title: 'DocReviewCard · 一处改动',
    about: '看着的那一处改动下面的卡：第几处，不要就还原。',
    file: 'src/components/panels/doc/DocReviewCard.vue',
    component: DocReviewCard,
    needs: T,
    states: [
      {
        name: '看着第几处',
        note: '「第 1/3 处」加一颗「还原这处」；忙着时那颗按不动。',
        props: { index: 0, total: 3, busy: false, editable: true },
        expect: '第 1/3 处',
      },
    ],
  },
  {
    id: 'panel-doc-thread-card',
    title: 'DocThreadCard · 一串评论',
    about:
      '一条评论串一张卡：上面是评的那几个字，下面是来回的每一句；AI 队友在答时说它到了哪一步。在看的那张才给回复框和解决。',
    file: 'src/components/panels/doc/DocThreadCard.vue',
    component: DocThreadCard,
    needs: T,
    states: [
      {
        name: '看着一串',
        note: '正在看这一串：下面开出回复框和「解决」。',
        props: {
          thread: docThreadState().threads[0],
          active: true,
          place: docPlaceOf(),
          activity: { state: 'working', tool: 'cheese_doc_edit' },
          busy: false,
          unknown: false,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          draftKey: 'demo-thread-open',
        },
        expect: '这里的用时要不要一起写进去',
      },
      {
        name: '芝士在答',
        note: '没在看这一串：它正在改文档，卡上说它到了哪一步，给「停止」。',
        props: {
          thread: docThreadState().threads[0],
          active: false,
          place: docPlaceOf(),
          activity: { state: 'working', tool: 'cheese_doc_edit' },
          busy: false,
          unknown: false,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          draftKey: 'demo-thread-working',
        },
        expect: '正在修改文档',
      },
      {
        name: '已经解决',
        note: '解决过的串：在看它时给的是「重新打开」，不再有回复框。',
        props: {
          thread: docThreadState().threads[1],
          active: true,
          place: docPlaceOf(),
          busy: false,
          unknown: false,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          draftKey: 'demo-thread-resolved',
        },
        expect: '重新打开',
      },
      {
        name: '写法不知道落没落',
        note: '上一次写的结果不知道：说一声，给「重新发送」。',
        props: {
          thread: docThreadState().threads[0],
          active: false,
          place: docPlaceOf(),
          busy: false,
          unknown: true,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          draftKey: 'demo-thread-unknown',
        },
        expect: '发送结果未知',
      },
    ],
  },
  {
    id: 'panel-doc-comments',
    title: 'DocComments · 评论列表',
    about: '评论栏里的内容：写评论的框，和一串串评论 —— 没解决的和已解决的分开看，每一串一张卡，对话直接摊开。',
    file: 'src/components/panels/doc/DocComments.vue',
    component: DocComments,
    needs: T,
    states: [
      {
        name: '没解决的',
        note: '看的是没解决的那些：一串人开的头，芝士接了一句。',
        props: {
          topicId: null,
          author: DOC_PERSON_HANDLE,
          threadState: docThreadState(),
          threadActions: DOC_THREAD_ACTIONS,
          openId: null,
          placeOf: docPlaceOf,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          filter: 'open',
        },
        expect: '这里的用时要不要一起写进去',
      },
      {
        name: '看已解决的',
        note: '切到「已解决」：只剩那一串已经处理掉的。',
        props: {
          topicId: null,
          author: DOC_PERSON_HANDLE,
          threadState: docThreadState(),
          threadActions: DOC_THREAD_ACTIONS,
          openId: null,
          placeOf: docPlaceOf,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          filter: 'resolved',
        },
        expect: '标题改成「上线过程」吧',
      },
      {
        name: '一条都没有',
        note: '这篇还没有任何评论：给一句空态，而不是空白。',
        props: {
          topicId: null,
          author: DOC_PERSON_HANDLE,
          threadState: docThreadStateEmpty(),
          threadActions: DOC_THREAD_ACTIONS,
          openId: null,
          placeOf: docPlaceOf,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
          filter: 'open',
        },
        expect: '暂无评论',
      },
    ],
  },
  {
    id: 'panel-doc-comment-panel',
    title: 'DocCommentPanel · 评论那一栏',
    about:
      '评论栏这一列：够宽时贴在正文右边，窄时盖在正文上面；宽度能拖。里面是 DocComments —— 打开、定位、关掉由外面叫它。',
    file: 'src/components/panels/doc/DocCommentPanel.vue',
    component: DocCommentPanel,
    needs: UI,
    states: [
      {
        name: '正文这一格',
        note: '面板自己管停靠、抽屉、宽度；平时它是一列贴在正文右边的框，里面装着正文（下面的槽）和评论。这里给的是槽里的正文。',
        slot: '九月起搜索框改为常驻，下面是两周后的复盘。',
        props: {
          topicId: null,
          author: DOC_PERSON_HANDLE,
          threadState: docThreadState(),
          threadActions: DOC_THREAD_ACTIONS,
          openId: null,
          placeOf: docPlaceOf,
          agentName: DOC_AGENT,
          mentionNames: DOC_MENTION_NAMES,
          nameOf: docNameOf,
          writable: true,
        },
        expect: '九月起搜索框改为常驻',
      },
    ],
  },
  {
    id: 'panel-doc-surface',
    title: 'DocSurface · 正文那一块',
    about:
      '文档正文本身：拿一份真的协同文档，用 tiptap 画出来，套上这套块、浮层和协同光标。顶栏、评论栏那些是外面的事。',
    file: 'src/components/panels/doc/DocSurface.vue',
    component: DocSurface,
    needs: UI,
    states: [
      {
        name: '打开一篇',
        note: '这一篇有标题「上线过程」：正文按块画出来，行首打「/」插块、选中字浮条上有 AI 队友和评论。',
        props: {
          editable: true,
          loading: false,
          session: docSurfaceSession(),
          topicId: null,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          fetchDocNodes: async () => [],
          imageSrc: (src: string) => src,
          pulse: () => {},
        },
        expect: '上线过程',
      },
      {
        name: '还在读',
        note: '正文还在路上：画骨架，编辑器让位。',
        props: {
          editable: false,
          loading: true,
          session: null,
          topicId: null,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          fetchDocNodes: async () => [],
          imageSrc: (src: string) => src,
          pulse: () => {},
        },
        expectSelector: '.doc-skel',
      },
    ],
  },
]
