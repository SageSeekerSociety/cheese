/**
 * `panels/doc/` 这一册：文档面板拆出来的那些零件 —— 顶栏、浮条、大纲、吐槽栏、
 * AI 队友那几张卡、修改记录、版本对比……一格一格摆出来。
 *
 * 单独一份，和 `catalogDoc.ts` 同一个理由：`catalog.ts` 已经九百行上下、接近 `frontend/src` 的文件规模上限；
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
  demoEditorStandalone,
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
import { t } from '@/i18n'

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
const editorForBar = demoEditorStandalone(DOC_MARKDOWN)
const editorForLink = demoEditor(DOC_MARKDOWN)

export const DOC_PANEL_ENTRIES: CatalogEntry[] = [
  {
    id: 'panel-doc-top-bar',
    title: 'DocTopBar · 文档顶栏',
    get about() {
      return t('catalog.panelDocTopBar.about')
    },
    file: 'src/components/panels/doc/DocTopBar.vue',
    component: DocTopBar,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocTopBar.s1.name')
        },
        get note() {
          return t('catalog.panelDocTopBar.s1.note')
        },
        props: topBarProps({ lastEdit: { name: DOC_PERSON, at: '2026-09-30T02:00:00.000Z' }, peers: DOC_PEERS }),
        expect: '编辑',
      },
      {
        get name() {
          return t('catalog.panelDocTopBar.s2.name')
        },
        get note() {
          return t('catalog.panelDocTopBar.s2.note')
        },
        props: topBarProps({ connection: 'offline' }),
        expect: '没连上',
      },
      {
        get name() {
          return t('catalog.panelDocTopBar.s3.name')
        },
        get note() {
          return t('catalog.panelDocTopBar.s3.note')
        },
        props: topBarProps({ editable: false, readOnly: true }),
        expect: '只读',
      },
    ],
  },
  {
    id: 'panel-doc-outline',
    title: 'DocOutline · 大纲',
    get about() {
      return t('catalog.panelDocOutline.about')
    },
    file: 'src/components/panels/doc/DocOutline.vue',
    component: DocOutline,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocOutline.s1.name')
        },
        get note() {
          return t('catalog.panelDocOutline.s1.note')
        },
        props: { headings: DOC_HEADINGS },
        expect: '上线过程',
      },
      {
        get name() {
          return t('catalog.panelDocOutline.s2.name')
        },
        get note() {
          return t('catalog.panelDocOutline.s2.note')
        },
        props: { headings: DOC_HEADINGS_EMPTY },
        expect: '这篇文档还没有标题',
      },
    ],
  },
  {
    id: 'panel-doc-presence',
    title: 'DocPresence · 在线的人',
    get about() {
      return t('catalog.panelDocPresence.about')
    },
    file: 'src/components/panels/doc/DocPresence.vue',
    component: DocPresence,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocPresence.s1.name')
        },
        get note() {
          return t('catalog.panelDocPresence.s1.note')
        },
        props: { peers: DOC_PEERS },
        expect: '+2',
      },
      {
        get name() {
          return t('catalog.panelDocPresence.s2.name')
        },
        get note() {
          return t('catalog.panelDocPresence.s2.note')
        },
        props: { peers: [DOC_PEERS[0]] },
        expectSelector: '.doc-presence__face:only-child',
      },
    ],
  },
  {
    id: 'panel-doc-edit-button',
    title: 'DocEditButton · 文档里的小按钮',
    get about() {
      return t('catalog.panelDocEditButton.about')
    },
    file: 'src/components/panels/doc/DocEditButton.vue',
    component: DocEditButton,
    needs: [],
    states: [
      {
        get name() {
          return t('catalog.panelDocEditButton.s1.name')
        },
        get note() {
          return t('catalog.panelDocEditButton.s1.note')
        },
        props: {},
        slot: '接受',
        expect: '接受',
      },
      {
        get name() {
          return t('catalog.panelDocEditButton.s2.name')
        },
        get note() {
          return t('catalog.panelDocEditButton.s2.note')
        },
        slot: '全部接受',
        props: { strong: true },
        expect: '全部接受',
      },
      {
        get name() {
          return t('catalog.panelDocEditButton.s3.name')
        },
        get note() {
          return t('catalog.panelDocEditButton.s3.note')
        },
        slot: '还原这处',
        props: { disabled: true },
        expect: '还原这处',
      },
    ],
  },
  {
    id: 'panel-doc-find-bar',
    title: 'DocFindBar · 文档内查找',
    get about() {
      return t('catalog.panelDocFindBar.about')
    },
    file: 'src/components/panels/doc/DocFindBar.vue',
    component: DocFindBar,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocFindBar.s1.name')
        },
        get note() {
          return t('catalog.panelDocFindBar.s1.note')
        },
        props: { open: true, query: '搜索', total: 5, current: 2 },
        expect: '2/5',
      },
      {
        get name() {
          return t('catalog.panelDocFindBar.s2.name')
        },
        get note() {
          return t('catalog.panelDocFindBar.s2.note')
        },
        props: { open: true, query: '不存在', total: 0, current: 0 },
        expect: '没有找到',
      },
    ],
  },
  {
    id: 'panel-doc-slash-menu',
    title: 'DocSlashMenu · 块类型菜单',
    get about() {
      return t('catalog.panelDocSlashMenu.about')
    },
    file: 'src/components/panels/doc/DocSlashMenu.vue',
    component: DocSlashMenu,
    needs: ['vuetify'],
    states: [
      {
        get name() {
          return t('catalog.panelDocSlashMenu.s1.name')
        },
        get note() {
          return t('catalog.panelDocSlashMenu.s1.note')
        },
        props: { items: DOC_SLASH_ITEMS.slice(0, 6), index: 1, top: 0, left: 0 },
        expect: '标题 1',
      },
    ],
  },
  {
    id: 'panel-doc-overlays',
    title: 'DocOverlays · 压在正文上的那几块',
    get about() {
      return t('catalog.panelDocOverlays.about')
    },
    file: 'src/components/panels/doc/DocOverlays.vue',
    component: DocOverlays,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocOverlays.s1.name')
        },
        get note() {
          return t('catalog.panelDocOverlays.s1.note')
        },
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
    get about() {
      return t('catalog.panelDocBubble.about')
    },
    file: 'src/components/panels/doc/DocBubble.vue',
    component: DocBubble,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocBubble.s1.name')
        },
        get note() {
          return t('catalog.panelDocBubble.s1.note')
        },
        props: {
          editor: editorForBubble,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: true,
          canAgent: true,
          canComment: true,
          hasSelection: true,
        },
        expectSelector: '.doc-bubble--float .mdi-link-variant',
      },
      {
        get name() {
          return t('catalog.panelDocBubble.s2.name')
        },
        get note() {
          return t('catalog.panelDocBubble.s2.note')
        },
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
        get name() {
          return t('catalog.panelDocBubble.s3.name')
        },
        get note() {
          return t('catalog.panelDocBubble.s3.note')
        },
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
        get name() {
          return t('catalog.panelDocBubble.s4.name')
        },
        get note() {
          return t('catalog.panelDocBubble.s4.note')
        },
        props: {
          editor: editorForBar,
          agentName: DOC_AGENT,
          agentHandle: DOC_AGENT_HANDLE,
          editable: true,
          canAgent: true,
          canComment: true,
          variant: 'bar',
          hasSelection: false,
        },
        expectSelector: '.doc-bubble--bar [aria-label="撤销"]',
      },
    ],
  },
  {
    id: 'panel-doc-link-callout',
    title: 'DocLinkCallout · 链接框',
    get about() {
      return t('catalog.panelDocLinkCallout.about')
    },
    file: 'src/components/panels/doc/DocLinkCallout.vue',
    component: DocLinkCallout,
    needs: UI,
    // 链接框本身按选区绝对定位；目录把它按正常文档流摆出来，免得浮到页头。
    args: { style: { position: 'static' } },
    states: [
      {
        get name() {
          return t('catalog.panelDocLinkCallout.s1.name')
        },
        get note() {
          return t('catalog.panelDocLinkCallout.s1.note')
        },
        props: {
          target: {
            editor: editorForLink,
            doc: editorForLink.state.doc,
            from: 1,
            to: 5,
            mark: null,
            href: '',
          },
        },
        expect: '保存链接',
      },
    ],
  },
  {
    id: 'panel-doc-version-diff',
    title: 'DocVersionDiff · 和上一版比',
    get about() {
      return t('catalog.panelDocVersionDiff.about')
    },
    file: 'src/components/panels/doc/DocVersionDiff.vue',
    component: DocVersionDiff,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.panelDocVersionDiff.s1.name')
        },
        get note() {
          return t('catalog.panelDocVersionDiff.s1.note')
        },
        props: { diff: DOC_DIFF },
        expect: '修改 1 段',
      },
      {
        get name() {
          return t('catalog.panelDocVersionDiff.s2.name')
        },
        get note() {
          return t('catalog.panelDocVersionDiff.s2.note')
        },
        props: { diff: DOC_DIFF_EMPTY },
        expect: '和上一版相比没有改动',
      },
    ],
  },
  {
    id: 'panel-doc-history',
    title: 'DocHistory · 修改记录',
    get about() {
      return t('catalog.panelDocHistory.about')
    },
    file: 'src/components/panels/doc/DocHistory.vue',
    component: DocHistory,
    needs: UI,
    teleport: true,
    states: [
      {
        get name() {
          return t('catalog.panelDocHistory.s1.name')
        },
        get note() {
          return t('catalog.panelDocHistory.s1.note')
        },
        props: {
          open: true,
          load: docVersionPage,
          editable: true,
          nameOf: docNameOf,
          mentionNames: DOC_MENTION_NAMES,
        },
        expectSelector: '.doc-history__item[aria-current="true"]',
      },
      {
        get name() {
          return t('catalog.panelDocHistory.s2.name')
        },
        get note() {
          return t('catalog.panelDocHistory.s2.note')
        },
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
    get about() {
      return t('catalog.panelDocAgentResult.about')
    },
    file: 'src/components/panels/doc/DocAgentResult.vue',
    component: DocAgentResult,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.panelDocAgentResult.s1.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s1.note')
        },
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
        get name() {
          return t('catalog.panelDocAgentResult.s2.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s2.note')
        },
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
        get name() {
          return t('catalog.panelDocAgentResult.s3.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s3.note')
        },
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
        get name() {
          return t('catalog.panelDocAgentResult.s4.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s4.note')
        },
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
        get name() {
          return t('catalog.panelDocAgentResult.s5.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s5.note')
        },
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
        get name() {
          return t('catalog.panelDocAgentResult.s6.name')
        },
        get note() {
          return t('catalog.panelDocAgentResult.s6.note')
        },
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
    get about() {
      return t('catalog.panelDocAgentBox.about')
    },
    file: 'src/components/panels/doc/DocAgentBox.vue',
    component: DocAgentBox,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocAgentBox.s1.name')
        },
        get note() {
          return t('catalog.panelDocAgentBox.s1.note')
        },
        props: { agentName: DOC_AGENT, scope: 'selection', context: DOC_PRESET_EDIT },
        expect: '润色',
      },
      {
        get name() {
          return t('catalog.panelDocAgentBox.s2.name')
        },
        get note() {
          return t('catalog.panelDocAgentBox.s2.note')
        },
        props: { agentName: DOC_AGENT, scope: 'document', context: DOC_PRESET_DOC },
        expect: '总结要点',
      },
      {
        get name() {
          return t('catalog.panelDocAgentBox.s3.name')
        },
        get note() {
          return t('catalog.panelDocAgentBox.s3.note')
        },
        props: { agentName: DOC_AGENT, scope: 'selection', context: { editable: false, list: false, chinese: true } },
        expectSelector: 'input[aria-label="输入问题"]',
      },
    ],
  },
  {
    id: 'panel-doc-suggestion-strip',
    title: 'DocSuggestionStrip · 建议那条',
    get about() {
      return t('catalog.panelDocSuggestionStrip.about')
    },
    file: 'src/components/panels/doc/DocSuggestionStrip.vue',
    component: DocSuggestionStrip,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocSuggestionStrip.s1.name')
        },
        get note() {
          return t('catalog.panelDocSuggestionStrip.s1.note')
        },
        props: { agentName: DOC_AGENT, count: 3, decided: { accepted: 0, rejected: 0 }, editable: true },
        expect: '还有 3 处待处理',
      },
      {
        get name() {
          return t('catalog.panelDocSuggestionStrip.s2.name')
        },
        get note() {
          return t('catalog.panelDocSuggestionStrip.s2.note')
        },
        props: { agentName: DOC_AGENT, count: 0, decided: { accepted: 2, rejected: 1 }, editable: true },
        expect: '接受 2 处，拒绝 1 处',
      },
    ],
  },
  {
    id: 'panel-doc-suggestion-card',
    title: 'DocSuggestionCard · 一处建议',
    get about() {
      return t('catalog.panelDocSuggestionCard.about')
    },
    file: 'src/components/panels/doc/DocSuggestionCard.vue',
    component: DocSuggestionCard,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.panelDocSuggestionCard.s1.name')
        },
        get note() {
          return t('catalog.panelDocSuggestionCard.s1.note')
        },
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
        get name() {
          return t('catalog.panelDocSuggestionCard.s2.name')
        },
        get note() {
          return t('catalog.panelDocSuggestionCard.s2.note')
        },
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
    get about() {
      return t('catalog.panelDocReviewStrip.about')
    },
    file: 'src/components/panels/doc/DocReviewStrip.vue',
    component: DocReviewStrip,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocReviewStrip.s1.name')
        },
        get note() {
          return t('catalog.panelDocReviewStrip.s1.note')
        },
        props: { agentName: DOC_AGENT, requester: DOC_PERSON, count: 3 },
        expect: '王长鑫让芝士改的 3 处',
      },
      {
        get name() {
          return t('catalog.panelDocReviewStrip.s2.name')
        },
        get note() {
          return t('catalog.panelDocReviewStrip.s2.note')
        },
        props: { agentName: DOC_AGENT, requester: DOC_PERSON, count: 0 },
        expect: '都已还原',
      },
    ],
  },
  {
    id: 'panel-doc-review-card',
    title: 'DocReviewCard · 一处改动',
    get about() {
      return t('catalog.panelDocReviewCard.about')
    },
    file: 'src/components/panels/doc/DocReviewCard.vue',
    component: DocReviewCard,
    needs: T,
    states: [
      {
        get name() {
          return t('catalog.panelDocReviewCard.s1.name')
        },
        get note() {
          return t('catalog.panelDocReviewCard.s1.note')
        },
        props: { index: 0, total: 3, busy: false, editable: true },
        expect: '第 1/3 处',
      },
    ],
  },
  {
    id: 'panel-doc-thread-card',
    title: 'DocThreadCard · 一串评论',
    get about() {
      return t('catalog.panelDocThreadCard.about')
    },
    file: 'src/components/panels/doc/DocThreadCard.vue',
    component: DocThreadCard,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocThreadCard.s1.name')
        },
        get note() {
          return t('catalog.panelDocThreadCard.s1.note')
        },
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
        get name() {
          return t('catalog.panelDocThreadCard.s2.name')
        },
        get note() {
          return t('catalog.panelDocThreadCard.s2.note')
        },
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
        get name() {
          return t('catalog.panelDocThreadCard.s3.name')
        },
        get note() {
          return t('catalog.panelDocThreadCard.s3.note')
        },
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
        get name() {
          return t('catalog.panelDocThreadCard.s4.name')
        },
        get note() {
          return t('catalog.panelDocThreadCard.s4.note')
        },
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
    get about() {
      return t('catalog.panelDocComments.about')
    },
    file: 'src/components/panels/doc/DocComments.vue',
    component: DocComments,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocComments.s1.name')
        },
        get note() {
          return t('catalog.panelDocComments.s1.note')
        },
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
        get name() {
          return t('catalog.panelDocComments.s2.name')
        },
        get note() {
          return t('catalog.panelDocComments.s2.note')
        },
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
        get name() {
          return t('catalog.panelDocComments.s3.name')
        },
        get note() {
          return t('catalog.panelDocComments.s3.note')
        },
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
    get about() {
      return t('catalog.panelDocCommentPanel.about')
    },
    file: 'src/components/panels/doc/DocCommentPanel.vue',
    component: DocCommentPanel,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocCommentPanel.s1.name')
        },
        get note() {
          return t('catalog.panelDocCommentPanel.s1.note')
        },
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
    get about() {
      return t('catalog.panelDocSurface.about')
    },
    file: 'src/components/panels/doc/DocSurface.vue',
    component: DocSurface,
    needs: UI,
    states: [
      {
        get name() {
          return t('catalog.panelDocSurface.s1.name')
        },
        get note() {
          return t('catalog.panelDocSurface.s1.note')
        },
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
        get name() {
          return t('catalog.panelDocSurface.s2.name')
        },
        get note() {
          return t('catalog.panelDocSurface.s2.note')
        },
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
