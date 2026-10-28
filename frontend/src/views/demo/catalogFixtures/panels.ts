/**
 * 工作面板那三格（#2143 拆出来的 View）：改动、预览、文档。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：三件都是「props 进、事件出」的纯渲染
 * 组件（取数在 `composables/usePanel*` 里），所以这一册造的全是数据 —— 挂起来不
 * 需要后端，也不需要登录。条目在 `../catalogPanels.ts`、`../catalog/panel-views.ts`。
 */
import type { DocConnection, DocPeer, DocSession } from '@/composables/useDocCollab'
import type { FileContent, FileSource, RoomTask, Topic, WorkspaceFile } from '@/cx_types'
import type { FileDiff } from '@/lib/diff'
import type { DocThreadActions, DocThreadState } from '@/lib/docThreadTypes'
import type { ChangesScene } from '../demoScene'

import { DEMO_PROJECT, DEMO_TOPIC, diffOf, filesOf, roomTask } from '../demoPanels'

import { AGENT_NAME } from './common'

import { parseDiffLines, splitDiffByFile } from '@/lib/diff'
import { localDocSession } from '@/lib/docLocalSession'
import { DOCUMENT_TYPES } from '@/lib/fileKind'
import { previewBundles, revisionsBundle } from '@/test/panelBundles'

// 这三件是「props 进、事件出」的纯渲染组件（取数在 `composables/usePanel*` 里），
// 所以下面造的全是数据 —— 挂起来不需要后端，也不需要登录。改动那一格的原料从剧本
// 里那几份真东西出发（`demoPanels` 的 `diffOf` / `filesOf` / `roomTask`），只有剧本
// 没演到的那几处（保存冲突、空、读不到）才补一小截。

/** 改动那一格看的这一支活：一份新文件、一份改过的、一份删掉的。 */
const CHANGES_SCENE: ChangesScene = {
  files: [
    {
      path: 'README.md',
      status: 'added',
      diff: ['+# 课程资料', '+', '+这个项目放本课程的课件和作业。', '+', '+有不清楚的地方，在话题里问。'],
    },
    {
      path: 'docs/week-1.md',
      status: 'modified',
      diff: [
        ' 第一周的课件放在这里。',
        '-习题答案：第 3、5、7 题',
        '+习题答案：第 3、5、7、9 题',
        '+',
        '+最后一题的提示写在下面。',
      ],
    },
    {
      path: 'docs/old-plan.md',
      status: 'removed',
      diff: ['-# 旧的大纲', '-', '-这一版已经不用了。'],
    },
  ],
}

const CHANGES_FILE_DIFFS = splitDiffByFile(diffOf(CHANGES_SCENE))
/** 打开的那一份（新文件，字都在正文里）。 */
const CHANGES_OPEN = CHANGES_FILE_DIFFS[0]
const CHANGES_BY_PATH = new Map(CHANGES_FILE_DIFFS.map((d) => [d.path, d]))
const CHANGES_TREE: WorkspaceFile[] = filesOf(CHANGES_SCENE)

/** 改动那一支活自己（面板顶上那条「来源」读的就是它）。 */
const CHANGES_TASK: RoomTask = roomTask(
  { id: 'demo-task-1', title: '整理第一周的课件', column: 'needs_you', phrase: 'awaiting_review' },
  0
)

const CHANGES_BASE = {
  // 修订清单那一包：这一格只画，清单本身另有它自己的用例（`RevisionList.library.spec.ts`）。
  revs: revisionsBundle(),
  topicId: DEMO_TOPIC,
  readOnly: false,
  taskLoadError: null,
  selectedTask: CHANGES_TASK.id,
  currentTask: CHANGES_TASK,
  sourceStatus: '待审阅',
  sourceUnavailable: false,
  fileSource: 'committed' as FileSource,
  fileToolReady: true,
  loading: false,
  refreshing: false,
  errorMsg: null,
  liveFallback: 'none' as const,
  noRepo: false,
  fileDiffs: CHANGES_FILE_DIFFS,
  diffByPath: CHANGES_BY_PATH,
  treeFiles: CHANGES_TREE,
  allFiles: CHANGES_TREE,
  openPath: CHANGES_OPEN.path,
  fileDraft: '# 课程资料\n\n这个项目放本课程的课件和作业。',
  fileSaving: false,
  fileDirty: false,
  fileVersion: 'cd1f2a3',
  fileBinary: false,
  fileTooLarge: false,
  fileBytes: 168,
  fileReadOnly: false,
  fileConflict: false,
  openDiff: CHANGES_OPEN,
  openDiffLines: parseDiffLines(CHANGES_OPEN.body),
  effectiveView: 'diff' as const,
  fileView: 'diff' as const,
  openIsImage: false,
  openIsDocument: false,
  openDocumentType: null,
  revisionPath: null,
  openImageSrc: '',
  openImageError: '',
  collapsedDirs: new Set<string>(),
  revealTick: 0,
  draftCount: 0,
  docBytes: null,
  docLoading: false,
  docError: '',
  docRendererMissing: false,
}

/** 改动那一格的整串 props（六十来样 —— 这就是它的全部环境）。 */
export function changesPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...CHANGES_BASE, ...over }
}

/** 手上什么都没有的那几样：没有 diff、没有树，右边也没打开任何文件 —— 取数
 *  那一层在那时真会是这些值（没有打开图片时 `openImageSrc` 是空串，横条上文件那半不摆）。 */
export const NOTHING_CHANGED = {
  fileDiffs: [],
  diffByPath: new Map<string, FileDiff>(),
  treeFiles: [],
  allFiles: [],
  openPath: null,
  openDiff: null,
  openDiffLines: [],
  fileDraft: '',
  fileVersion: null,
  fileBytes: 0,
  openImageSrc: '',
  collapsedDirs: new Set<string>(),
  fileToolReady: false,
}

/** 空的那一格：这一支活什么都没改。 */
export const CHANGES_EMPTY = changesPanelProps(NOTHING_CHANGED)

/** 没绑仓库：取数那一层问到这一点就不再取树和提交（`loadAll` 在 `noRepo` 上返回），
 *  `fileToolReady` 本身也看着 `noRepo`。 */
export const NO_REPO = { ...NOTHING_CHANGED, noRepo: true }

/** 预览那一格看的这一份：一篇 markdown，正文直接画出来。 */
const PREVIEW_FILE: FileContent = {
  path: 'docs/week-1.md',
  content: '# 第一周\n\n课件和作业都在这里。\n\n- 课件：前三讲已经排好\n- 作业：第 3、5、7、9 题\n',
  version: '9f8e7d6',
  bytes: 132,
  binary: false,
  too_large: false,
  source: 'live',
}

const PREVIEW_BASE = {
  // 修订清单、那一栏历史、编辑器那一份会话：预览站里它们只画，取数在 `usePanelPreview`。
  ...previewBundles(),
  topicId: DEMO_TOPIC,
  projectId: DEMO_PROJECT,
  frameName: 'cheese-preview-demo',
  loading: false,
  refreshing: false,
  previewFile: PREVIEW_FILE,
  previewMime: 'text/markdown',
  previewNamed: true,
  previewUrl: null,
  previewAppNote: '',
  previewTunnelUp: true,
  previewNamedPath: PREVIEW_FILE.path,
  previewError: null,
  previewReadError: null,
  documentSuffix: 'md',
  documentType: DOCUMENT_TYPES.md,
  documentName: PREVIEW_FILE.path,
  isImageArtifact: false,
  downloadError: '',
  docBytes: null,
  docLoading: false,
  docError: '',
  docRendererMissing: false,
}

/** 预览那一格的整串 props。 */
export function previewPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...PREVIEW_BASE, ...over }
}

/** 没有东西可看的那一格（房间里还没摆出过任何东西）。 */
export const PREVIEW_EMPTY = previewPanelProps({
  previewFile: null,
  documentType: null,
  documentName: '',
  previewMime: '',
  previewNamed: false,
  previewNamedPath: '',
})

/** 文档那一格看的这一篇。 */
export const DOC_TOPIC: Topic = {
  id: DEMO_TOPIC,
  project_id: DEMO_PROJECT,
  parent_id: null,
  title: '课程资料',
  kind: 'topic',
  status: 'open',
  created_at: '2026-09-29T09:00:00Z',
}

/** 这一格接住的动作：真产品里它们落回 `usePanelDoc` 的 ref，预览站里什么都不做。 */
const noop = () => {}
const noopAsync = async () => {}

const DOC_BASE = {
  topic: DOC_TOPIC,
  activityTick: 0,
  agentName: AGENT_NAME,
  topicList: [DOC_TOPIC],
  session: null as DocSession | null,
  editable: true,
  readOnly: false,
  loading: false,
  connection: 'connected' as DocConnection,
  peers: [] as DocPeer[],
  errorMsg: null,
  threadState: { threads: [], activity: {}, errors: {}, busy: false, unknown: null } as DocThreadState,
  threadActions: {
    reply: noopAsync,
    resolve: noopAsync,
    reopen: noopAsync,
    recover: async () => undefined,
    stopAgent: noopAsync,
  } as DocThreadActions,
  fetchDocNodes: async () => [],
  imageSrc: async (src: string) => src,
  toggleEditable: noop,
  setError: noop,
}

/** 一篇只活在这一页里的协同文档：预览站上正文是它，不连任何服务。 */
export function docSession(markdown: string): DocSession {
  return localDocSession(markdown, DEMO_TOPIC)
}

/** 文档那一格的整串 props。 */
export function docPanelProps(): typeof DOC_BASE
export function docPanelProps<T extends Record<string, unknown>>(over: T): Omit<typeof DOC_BASE, keyof T> & T
export function docPanelProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...DOC_BASE, ...over }
}
