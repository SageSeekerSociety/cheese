/**
 * `panels/doc/` 这一册的前一半：文档面板拆出来的那些零件 —— 顶栏、浮条、大纲、吐槽框、
 * 悬浮层、链接框、版本对比、修改记录，一格一格摆出来。
 *
 * 和 `catalogPanelsDocPartB.ts` 一起构成这一册；两份都由 `catalogPanelsDoc.ts` 合并成
 * `DOC_PANEL_ENTRIES` 交给 `catalogPanels.ts`。样例数据在 `catalogPanelsDocFixtures.ts`。
 *
 * 编辑器那几件（浮条、slash 菜单、链接框）拿的是**真的** tiptap 编辑器（`demoEditor`），
 * 不是空壳：它们要的编辑器身上的状态，只有真编辑器给得出。
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
  DOC_SLASH_ITEMS,
  docNameOf,
  docVersionPage,
  docVersionPageEmpty,
} from './catalogPanelsDocFixtures'

import DocBubble from '@/components/panels/doc/DocBubble.vue'
import DocEditButton from '@/components/panels/doc/DocEditButton.vue'
import DocFindBar from '@/components/panels/doc/DocFindBar.vue'
import DocHistory from '@/components/panels/doc/DocHistory.vue'
import DocLinkCallout from '@/components/panels/doc/DocLinkCallout.vue'
import DocOutline from '@/components/panels/doc/DocOutline.vue'
import DocOverlays from '@/components/panels/doc/DocOverlays.vue'
import DocPresence from '@/components/panels/doc/DocPresence.vue'
import DocSlashMenu from '@/components/panels/doc/DocSlashMenu.vue'
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

export const DOC_PANEL_ENTRIES_A: CatalogEntry[] = [
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
]
