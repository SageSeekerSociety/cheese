/**
 * 「预览面板」那一册的样例数据：一张图、一叠幻灯片、一份 CSV、一份 Word、一栏修订、
 * 一栏保存历史、一帧跑着的应用，和几个取数的替身。
 *
 * 单独一份，和 `catalogPanelsFixtures.ts` 同一个理由：那一份已经够长，而这些只被
 * `catalogPanelsPreview.ts` 用。能用真类型的地方就用真类型（`DocumentRevision`、
 * `RoomFileRevision`、`SketchStroke`），预览站里画出来的也就是真的那种样子。
 */
import type { RoomFileEditorSession, RoomFileRevision } from '@/api'
import type { PenStroke, ShapeStroke } from '@/components/panels/preview/designSketch'
import type { ImageRegionHandle, ImageRegionTarget } from '@/components/panels/preview/usePreviewImageRegion'
import type { PreviewCommentsBundle } from '@/composables/panelPreview/comments'
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { PreviewFrame } from '@/composables/usePreviewFrames'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'
import type { DocumentRevision, FileContent, ProjectMemberRow } from '@/cx_types'
import type { DocComment, DocThread, DocThreadActions, DocThreadState } from '@/lib/docThreadTypes'

import { computed, reactive, ref, shallowRef } from 'vue'

import { useDocPeople } from '@/composables/useDocPeople'

import { AGENT_NAME } from './catalogFixtures/common'
import { previewPanelProps } from './catalogFixtures'
import { DEMO_TOPIC } from './demoPanels'

import { fileHistoryBundle, revisionsBundle, roomFileEditorBundle } from '@/test/panelBundles'

/** 一张 1×1 的透明 PNG：图片预览要一个真的 src，但不需要真去加载。 */
export const PREVIEW_IMAGE_SRC =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

/** 一份 CSV：读者看到的是摆好的那张表，不是解析过程。 */
export function previewCsvBytes(): ArrayBuffer {
  return new TextEncoder().encode('姓名,分数\n张三,90\n李四,85\n').buffer as ArrayBuffer
}

/** 图上的一条自由画笔。 */
export const SKETCH_PEN: PenStroke = {
  tool: 'pen',
  color: '#E03131',
  width: 4,
  points: [
    { x: 20, y: 20 },
    { x: 80, y: 60 },
    { x: 140, y: 30 },
  ],
}

/** 图上的一块矩形标注（带一句写在上面的字）。 */
export const SKETCH_RECT: ShapeStroke = {
  tool: 'rect',
  color: '#1971C2',
  width: 3,
  region: { x: 60, y: 90, width: 160, height: 80 },
  text: '这块要改',
}

/** 三处待处理的修订。 */
export function revisionsWith(): DocumentRevisionsBundle {
  const revisions: DocumentRevision[] = [
    { number: 1, paragraph: 3, kind: 'replace', added: '60 天', removed: '30 天', author: '芝士', date: '2026-09-30' },
    {
      number: 2,
      paragraph: 5,
      kind: 'insert',
      added: '另加一句说明。',
      removed: '',
      author: '芝士',
      date: '2026-09-30',
    },
    {
      number: 3,
      paragraph: 8,
      kind: 'delete',
      added: '',
      removed: '这段先不要了。',
      author: '芝士',
      date: '2026-09-30',
    },
  ]
  return revisionsBundle({ revisions: ref<DocumentRevision[]>(revisions) })
}

const FILE_ROWS: RoomFileRevision[] = [
  {
    id: 'rev-3',
    path: 'readme.md',
    seq: 3,
    version: 'revision-3',
    size: 2048,
    author: 'wang',
    author_kind: 'human',
    source: 'editor',
    note: '改了开头一段',
    editor_key: null,
    created_at: '2026-09-30T02:00:00.000Z',
  },
  {
    id: 'rev-2',
    path: 'readme.md',
    seq: 2,
    version: 'revision-2',
    size: 1980,
    author: 'cheese',
    author_kind: 'agent',
    source: 'ai',
    note: '',
    editor_key: null,
    created_at: '2026-09-29T10:00:00.000Z',
  },
]

/** 一栏有过几次保存的保存历史。 */
export function fileHistoryWith(): RoomFileHistoryBundle {
  return fileHistoryBundle({ rows: ref<RoomFileRevision[]>(FILE_ROWS) })
}

/** 一个不能在线编辑的会话（这份格式只让看）。 */
export function fileEditorUnsupported() {
  const session: RoomFileEditorSession = { enabled: false, reason: 'unsupported' }
  return roomFileEditorBundle({ session: ref<RoomFileEditorSession | null>(session) })
}

/** 一篇 Word：读不成文本（`content` 是 null，字节由内容域发出来），所以它走文档
 *  那一档里「转成 PDF 再看」那条路。 */
export const PREVIEW_DOC_FILE: FileContent = {
  path: 'docs/课程大纲.docx',
  content: null,
  version: '3c2b1a0',
  bytes: 24576,
  binary: true,
  too_large: false,
  source: 'live',
}

/** 台前那一帧：一个跑着的应用。
 *
 *  `label`、`mime`、运行状态都给了值，因为应用条和那行运行状态画的正是它们。 */
export function previewFrame(over: Partial<PreviewFrame> = {}): PreviewFrame {
  return {
    id: 1,
    name: 'cheese-preview-demo',
    url: 'http://127.0.0.1:4173/',
    label: '预览 · 第一版',
    mime: 'text/html',
    version: '9f8e7d6',
    live: true,
    posted: true,
    resourceId: 'af31c9e0d4b2',
    instance: 'inst-7c41a9',
    runtime: 'ready',
    connection: 'online',
    // 装了运行时桥的应用：地址栏写的是它此刻的地址，后退前进也亮得起来（和原型里那一行
    // 同一个样子）。没装桥的页面这两样都不给，地址栏退回 label。
    bridged: true,
    href: 'http://127.0.0.1:4173/signup?step=2',
    title: '第 2 步：填写信息',
    canBack: true,
    canForward: false,
    ...over,
  }
}

/** 顶栏那一档要的那几样：从预览面板那一整包里挑，免得两处各写一份示例值。 */
export function headBarProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const all = previewPanelProps()
  return {
    topicId: all.topicId,
    projectId: all.projectId,
    previewUrl: null,
    previewFile: all.previewFile,
    fullscreenSupported: true,
    isImageArtifact: false,
    previewFull: false,
    refreshing: false,
    ...over,
  }
}

/** 应用那一档（网页/应用帧）要的那几样。 */
export function frameStageProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const all = previewPanelProps()
  const merged: Record<string, unknown> = {
    topicId: all.topicId,
    frameName: all.frameName,
    previewFile: null,
    previewMime: 'text/html',
    // 这里给的是「应用跑着」那一档的输入，所以带一个地址：产品里 `previewAppUp` 是
    // `!!url && !!tunnel_up` 算出来的（`panelPreview/currentPreview.ts`），没有地址就没有
    // 应答着的应用——「有端口应答却没有地址」是产品到不了的状态，夹具别造它。
    previewUrl: 'http://127.0.0.1:4173/',
    previewAppNote: '',
    previewTunnelUp: true,
    previewError: null,
    previewReadError: null,
    pickTarget: null,
    pickOn: false,
    agentName: AGENT_NAME,
    ...over,
  }
  // 应用有没有应答一律按产品那套算，不给它留一个能单独覆盖的口子：通道在、地址也
  // 在，才算有应答。要摆睡着那几档，把 `previewTunnelUp` 或 `previewUrl` 说成假的就够
  // 了——「通道在、有地址、应用却不答」是产品到不了的状态。
  merged.previewAppUp = !!merged.previewTunnelUp && !!merged.previewUrl
  return merged
}

/** 文档那一档要的那几样：默认是那篇 markdown。 */
export function docStageProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const all = previewPanelProps()
  return {
    topicId: all.topicId,
    projectId: all.projectId,
    documentType: all.documentType,
    documentName: all.documentName,
    documentSuffix: all.documentSuffix,
    previewFile: all.previewFile,
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    revs: revisionsWith(),
    fileHistory: fileHistoryWith(),
    showHistory: false,
    ...over,
  }
}

/** 图片那一档拿的那个句柄 —— `usePreviewImageRegion` 露出来的那一套。
 *
 *  真去调那个 composable 得在组件里（它要一份 props，还要一个卸载钩子），所以这里照
 *  它的形状搭一个：写成 `satisfies ImageRegionHandle`，它的形状一变得这里当场红。 */
export function imageRegionWith(over: { src?: string; identity?: string } = {}): ImageRegionHandle {
  return {
    imageSrc: ref(over.src ?? PREVIEW_IMAGE_SRC),
    imageIdentity: computed(() => over.identity ?? '设计稿.png'),
    selectionEnabled: computed(() => true),
    target: shallowRef<ImageRegionTarget | null>(null),
    capture: () => null,
    message: () => null,
    clear: () => {},
  } satisfies ImageRegionHandle
}

/** 图片那一档要的那几样：默认这张图已经铺上去了。 */
export function imageStageProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    documentName: '设计稿.png',
    previewFull: false,
    imageRegion: imageRegionWith(),
    locatorNote: '',
    locator: null,
    docError: '',
    annotateError: '',
    ...over,
  }
}

// ---- 这一份文件上的评论 -------------------------------------------------------

/** 评论挂的那份文件：预览台上摆着的那一篇 markdown。 */
export const PREVIEW_COMMENT_PATH = 'docs/week-1.md'
export const PREVIEW_COMMENT_VERSION = '9f8e7d6'

/**
 * 这一栏里的名册：两个人，一个挑过头像（`avatar_id` 3），一个没挑（null）。
 *
 * 没挑过的那一位按规矩画彩色首字母 —— 不退回全站默认脸，那正是这一栏以前的毛病。
 * 预览站里没有头像服务，挑过的那个也可能取不到图，`UserAvatar` 那时同样退到首字母；
 * 要看的不是一张图，是「名字从名册读、头像按人各归各的」这条接线。
 */
const COMMENT_ROSTER: ProjectMemberRow[] = [
  { user_handle: 'wang', name: '王长鑫', role: 'owner', source: 'owner', avatar_id: 3 },
  { user_handle: 'li', name: '李甘', role: 'member', source: 'team', avatar_id: null },
]

/** 名册那一包（真的 `useDocPeople`）：名字和头像都从这里读，和产品里同一处。 */
function commentPeople() {
  return useDocPeople({
    members: () => COMMENT_ROSTER,
    agentHandle: () => 'cheese',
    agentName: () => AGENT_NAME,
  })
}

/** 一条评论：锚点记着它写在哪一版上，版本对得上，位置就还算数（`marked`）。 */
function comment(id: string, author: string, content: string, at: string, quote: string | null = null): DocComment {
  return { id, author, content, anchor_quote: quote, anchor: { version: PREVIEW_COMMENT_VERSION }, created_at: at }
}

/** 两串评：一串人开的头、芝士在答；一串已经解决。 */
export function previewCommentThreads(): DocThreadState {
  const threads: DocThread[] = [
    {
      comment: comment('fc-1', 'li', '这里的用时要不要一起写进去？', '2026-09-30T02:00:00.000Z', '搜索次数翻倍。'),
      revision: 2,
      state: 'open',
      replies: [
        {
          sequence: 1,
          comment: comment('fc-1-1', 'wang', '写进去了，改成「用时少了一秒」。', '2026-09-30T02:04:00.000Z'),
        },
        {
          sequence: 2,
          comment: comment('fc-1-2', 'cheese', '这一版已经同步到正文了。', '2026-09-30T02:06:00.000Z'),
        },
      ],
      answering: 'working',
    },
    {
      comment: comment('fc-2', 'wang', '课件和作业分开两页放吧。', '2026-09-29T08:00:00.000Z'),
      revision: 1,
      state: 'resolved',
      replies: [],
    },
  ]
  return { threads, activity: { 'fc-1': { state: 'working' } }, errors: {}, busy: false, unknown: null }
}

/** 预览站里写评论、解决、重开都不写：不动的空动作。 */
const COMMENT_ACTIONS: DocThreadActions = {
  reply: async () => {},
  resolve: async () => {},
  reopen: async () => {},
  recover: async () => undefined,
  stopAgent: async () => {},
}

/** `PreviewComments` 那一栏的整串 props。 */
export function previewCommentsProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const people = commentPeople()
  return {
    topicId: DEMO_TOPIC,
    path: PREVIEW_COMMENT_PATH,
    version: PREVIEW_COMMENT_VERSION,
    author: 'wang',
    threadState: previewCommentThreads(),
    threadActions: COMMENT_ACTIONS,
    pick: null,
    writable: true,
    post: async () => {},
    agentName: AGENT_NAME,
    nameOf: (handle: string) => people.names.value[handle] ?? handle,
    avatarOf: (handle: string) => people.avatars.value[handle] ?? '',
    ...over,
  }
}

/**
 * 预览那一格递下去的评论包（`usePanelPreviewComments` 的返回）：开着没有是这一格
 * 自己的状态，`toggle` 真的翻它 —— 于是预览站上那颗「评论」和栏里的 × 都点得动。
 */
export function previewCommentsBundle(over: { open?: boolean } = {}): PreviewCommentsBundle {
  const people = commentPeople()
  const open = ref(over.open ?? false)
  return {
    target: computed(() => ({ topicId: DEMO_TOPIC, path: PREVIEW_COMMENT_PATH, version: PREVIEW_COMMENT_VERSION })),
    open,
    toggle: () => {
      open.value = !open.value
    },
    author: 'wang',
    threads: { state: reactive(previewCommentThreads()), actions: COMMENT_ACTIONS, refresh: async () => {} },
    post: async () => {},
    nameOf: (handle: string) => people.names.value[handle] ?? handle,
    avatarOf: (handle: string) => people.avatars.value[handle] ?? '',
  }
}
