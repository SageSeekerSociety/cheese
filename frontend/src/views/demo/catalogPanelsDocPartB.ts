/**
 * `panels/doc/` 这一册的后一半：AI 队友那几张卡、建议条与建议卡、评审条与评审卡、讨论卡、
 * 评论、评论面板、正文面，一格一格摆出来。
 *
 * 和 `catalogPanelsDocPartA.ts` 一起构成这一册；两份都由 `catalogPanelsDoc.ts` 合并成
 * `DOC_PANEL_ENTRIES`。样例数据在 `catalogPanelsDocFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  DOC_AGENT,
  DOC_AGENT_HANDLE,
  DOC_MENTION_NAMES,
  DOC_PERSON,
  DOC_PERSON_HANDLE,
  DOC_PRESET_DOC,
  DOC_PRESET_EDIT,
  DOC_THREAD_ACTIONS,
  docNameOf,
  docPlaceOf,
  docSurfaceSession,
  docThreadState,
  docThreadStateEmpty,
} from './catalogPanelsDocFixtures'

import DocAgentBox from '@/components/panels/doc/DocAgentBox.vue'
import DocAgentResult from '@/components/panels/doc/DocAgentResult.vue'
import DocCommentPanel from '@/components/panels/doc/DocCommentPanel.vue'
import DocComments from '@/components/panels/doc/DocComments.vue'
import DocReviewCard from '@/components/panels/doc/DocReviewCard.vue'
import DocReviewStrip from '@/components/panels/doc/DocReviewStrip.vue'
import DocSuggestionCard from '@/components/panels/doc/DocSuggestionCard.vue'
import DocSuggestionStrip from '@/components/panels/doc/DocSuggestionStrip.vue'
import DocSurface from '@/components/panels/doc/DocSurface.vue'
import DocThreadCard from '@/components/panels/doc/DocThreadCard.vue'
import { t } from '@/i18n'

const UI: CatalogNeed[] = ['vuetify', 'i18n']
const T: CatalogNeed[] = ['i18n']

export const DOC_PANEL_ENTRIES_B: CatalogEntry[] = [
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
