/**
 * 工作面板那一组（`components/panels/*.vue` 里还没进预览站的那几件）吃的数据。
 *
 * 出发点和 `catalogFixtures.ts` 一样：能从剧本或现成的夹具里算出来的，就不手写。
 *
 *   - 改动那几件（`ChangesDiff`、`ChangesFileTree`、`ProjectFileView`、`PanelChanges`）
 *     读的是 `changesPanelProps()` 背后那一支活 —— 同一份 diff 切出来的树和逐行，
 *     树上的 +N −M 和点开看到的那一段因此不会对不上；
 *   - 现场、步骤清单读的是 `quickstart` 剧本放到某一步的那一帧（`frameAt`），和
 *     `DemoRoom` 上演的是同一串行；
 *   - 四只壳子（`PanelChanges` / `PanelPreview` / `PanelDoc` / `PanelSite`）收的是取数
 *     那一层的整包，这里把 View 夹具那一串 props 逐键装回 composable 返回的形状：状态
 *     是 ref 或 computed（和产品里那一样一致），动作是什么都不做的函数。一包里有哪几样
 *     由返回类型守着，少一样 typecheck 就报错；壳子只是把包摊开递给 View，所以画出来
 *     的就是 View 那几格。
 *   - 剧本没演到的（定时规则、支线、很长的一份 diff），照各自的类型造，人和房间仍然
 *     是剧本里那几位。
 *
 * 单独一份文件：`catalogFixtures.ts` 已经九百多行，再加会顶到一千行的上限。条目在
 * `catalogPanels.ts`。
 */
import type { Ref } from 'vue'
import type { DocThreadsBundle } from '@/composables/useDocThreads'
import type { PanelChangesBundle } from '@/composables/usePanelChanges'
import type { PanelDocBundle } from '@/composables/usePanelDoc'
import type { PanelPreviewBundle } from '@/composables/usePanelPreview'
import type { PanelSiteBundle } from '@/composables/usePanelSite'
import type { SessionInspectorBundle, SessionRead } from '@/composables/useSessionInspector'
import type { Block, RoomTask, TodoItem, WorkspaceFile } from '@/cx_types'
import type { DiffLine, FileDiff } from '@/lib/diff'
import type { DocumentIdentity } from '@/lib/documentIdentity'
import type { Routine, RoutineRun } from '@/lib/routine'
import type { SiteTurn } from '@/lib/siteLog'
import type { AgentControlState } from '@/types/agentControl'
import type { ThreadReply, ThreadRow } from '@/types/threads'

import { computed, reactive, ref, shallowRef } from 'vue'

import { useDocPeople } from '@/composables/useDocPeople'

import { memoryCanvas } from './catalogCanvasFixtures'
import {
  ACCEPT_REVIEWERS,
  AGENT_NAME,
  changesPanelProps,
  docPanelProps,
  previewPanelProps,
  ROOM_REFS,
} from './catalogFixtures'
import { DEMO_PROJECT, DEMO_TOPIC, diffOf } from './demoPanels'
import { frameAt } from './demoScene'
import { SCENES } from './scenes'

import { buildFileRows } from '@/lib/changesTree'
import { DIFF_WINDOW, parseDiffLines, splitDiffByFile } from '@/lib/diff'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, pageViewOf, suffixOf } from '@/lib/fileKind'
import { groupByTurn } from '@/lib/siteLog'

const SCENE = SCENES.quickstart

/** 剧本放到第 step 步末尾的那一帧（同 `catalogFixtures.ts` 里那个）。 */
function frame(step: number) {
  return frameAt(SCENE, step, Number.MAX_SAFE_INTEGER)
}

/** 剧本里的人：handle → 名字（王长鑫、芝士）。 */
export const PANEL_NAMES: Record<string, string> = ROOM_REFS.mentionNames

const noop = () => {}
const noopAsync = async () => {}
/** 要写点什么回去的动作（发评论、传标注、落修改）：预览站只画，不写，点到了就说一声。 */
const refuseWrite = () => Promise.reject(new Error('组件预览站不写任何东西'))

// ---- 改动那一支活 ----------------------------------------------------------

const CHANGES = changesPanelProps()
/** 这一支活改到的每一份文件各自那一段（`ChangesDiffList` 吃的就是这个）。 */
export const FILE_DIFFS = CHANGES.fileDiffs as FileDiff[]
const DIFF_BY_PATH = CHANGES.diffByPath as Map<string, FileDiff>
const TREE_FILES = CHANGES.treeFiles as WorkspaceFile[]
/** 这一支活本身（「不在当前版本里」那一格列的就是它）。 */
export const CHANGES_TASK = CHANGES.currentTask as RoomTask

/** 改动那一格拆出来的三个面，各自吃的那一串 props：从同一支活（`CHANGES`）里挑它要
 *  的键，值不另写一遍 —— 树、差异、打开着的那一份本来就是同一份数据切出来的。 */
export const CHANGES_BAR_PROPS = {
  openPath: CHANGES.openPath,
  sourceStatus: CHANGES.sourceStatus,
  fileDirty: CHANGES.fileDirty,
  openDiff: CHANGES.openDiff,
  openIsDocument: CHANGES.openIsDocument,
  effectiveView: CHANGES.effectiveView,
  fileReadOnly: CHANGES.fileReadOnly,
  fileSaving: CHANGES.fileSaving,
  currentTask: CHANGES.currentTask,
  fileSource: CHANGES.fileSource,
  fileToolReady: CHANGES.fileToolReady,
  refreshing: CHANGES.refreshing,
}

/** 打开着的那一份的正文（`ChangesFileContent`）。 */
export const CHANGES_CONTENT_PROPS = {
  openPath: CHANGES.openPath,
  openIsDocument: CHANGES.openIsDocument,
  docLoading: CHANGES.docLoading,
  docBytes: CHANGES.docBytes,
  docError: CHANGES.docError,
  docRendererMissing: CHANGES.docRendererMissing,
  openDocumentType: CHANGES.openDocumentType,
  revs: CHANGES.revs,
  revisionPath: CHANGES.revisionPath,
  review: CHANGES.review,
  canCompare: CHANGES.canCompare,
  comparing: CHANGES.comparing,
  comparison: CHANGES.comparison,
  comparisonLoading: CHANGES.comparisonLoading,
  comparisonError: CHANGES.comparisonError,
  effectiveView: CHANGES.effectiveView,
  openDiffLines: CHANGES.openDiffLines,
  openIsImage: CHANGES.openIsImage,
  openRawUrl: CHANGES.openRawUrl,
  fileBinary: CHANGES.fileBinary,
  fileTooLarge: CHANGES.fileTooLarge,
  fileBytes: CHANGES.fileBytes,
  fileDraft: CHANGES.fileDraft,
  fileReadOnly: CHANGES.fileReadOnly,
}

/** 全部改动那一面（`ChangesAllView`）。树常驻那一档在这里是关的：树常驻 / 只在顶部
 *  那块下面列几行，两种排法在目录里各有一格。 */
export const CHANGES_ALL_PROPS = {
  treeFiles: CHANGES.treeFiles,
  diffByPath: CHANGES.diffByPath,
  fileDiffs: CHANGES.fileDiffs,
  treeShown: false,
  currentTask: CHANGES.currentTask,
  sourceStatus: CHANGES.sourceStatus,
  fileSource: CHANGES.fileSource,
  fileToolReady: CHANGES.fileToolReady,
  refreshing: CHANGES.refreshing,
  summaryAll: false,
  review: CHANGES.review,
}

/** 这一支活里某一份文件自己的那一段，切成行（`ChangesDiff` 吃的就是这个）。 */
export function diffLinesOf(path: string): DiffLine[] {
  const diff = FILE_DIFFS.find((d) => d.path === path)
  return diff ? parseDiffLines(diff.body) : []
}

/** 一份超过 `DIFF_WINDOW` 行的新文件：成绩表一口气加了几百行。照样走 `diffOf` →
 *  `splitDiffByFile` → `parseDiffLines` 那条管线，不手拼 hunk 头。 */
export const LONG_DIFF_LINES: DiffLine[] = parseDiffLines(
  splitDiffByFile(
    diffOf({
      files: [
        {
          path: 'grades/week-1.csv',
          status: 'added',
          diff: [
            '+学号,姓名,第一周作业',
            ...Array.from(
              { length: DIFF_WINDOW + 40 },
              (_, i) => `+2026${String(i + 1).padStart(3, '0')},同学 ${i + 1},${80 + (i % 20)}`
            ),
          ],
        },
      ],
    })
  )[0].body
)

/** 工作区里这时有的文件：这一支活碰过的那几份（删掉的那份已经不在了），加上它没碰的
 *  —— 「打开其他文件」从这里挑。 */
export const WORKSPACE_FILES: WorkspaceFile[] = [
  ...TREE_FILES.filter((f) => DIFF_BY_PATH.get(f.path)?.status !== 'removed'),
  { path: 'syllabus.md', bytes: 1840 },
  { path: 'docs/week-2.md', bytes: 612 },
  { path: 'slides/week-1.pdf', bytes: 2_310_144 },
]

/** 文件树那几行：和 `PanelChangesView` 一样交给 `buildFileRows` 折。树上的文件照
 *  `usePanelChanges` 的 `treeFiles` 取：这一支改过的那几份（删掉的也在，那也是要审阅的
 *  一条）。 */
export function fileTreeRows(collapsed: string[] = []) {
  return buildFileRows({ files: TREE_FILES, diffByPath: DIFF_BY_PATH, collapsedDirs: new Set(collapsed) })
}

/** `ProjectFileView` 的那十八样：默认是一份读得到的 markdown（这一支活里那份 week-1）。 */
export function projectFileProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    path: 'docs/week-1.md',
    lines: null,
    loading: false,
    error: null,
    missing: false,
    content: '第一周的课件放在这里。\n习题答案：第 3、5、7、9 题\n\n最后一题的提示写在下面。\n',
    bytes: 96,
    binary: false,
    tooLarge: false,
    tasks: [],
    isImage: false,
    isDocument: false,
    documentType: null,
    rawUrl: `/api/projects/${DEMO_PROJECT}/file/raw?path=docs/week-1.md`,
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    ...over,
  }
}

// ---- 四只壳子收的那几包 ------------------------------------------------------
//
// 每一包都逐键写出来，返回类型就是 composable 的 `ReturnType`：少一样、多一样、名字拼
// 错、该是 computed 的给成了 ref，typecheck 当场点名。整包不用 `...` 摊一串 Record、
// 也不 `as` 收口 —— 那样漏掉的键在运行时是 undefined，壳子照样摊开递下去，View 多半
// 还画得出这几格，测试看不出来。唯一的 `as` 是读 View 夹具那一处（它是按 View props
// 写的 Record）：值缺了由 catalog.spec 挂 View 时的 props 校验报出来。

/** 一包摊开以后 View 拿到的那一串值：每个 ref 解开成它装的东西，其余（动作、整包）原样。
 *  View 夹具（`changesPanelProps` 那几个）写的就是这个形状。 */
type Unwrapped<B> = { [K in keyof B]: B[K] extends Ref<infer T> ? T : B[K] }

/** 一格能改的那几样：一包里的数据，不含动作（动作在包里统一是空操作，传进来也不会用）。 */
type Overrides<B> = Partial<{
  [K in keyof Unwrapped<B> as Unwrapped<B>[K] extends (...args: never[]) => unknown ? never : K]: Unwrapped<B>[K]
}>

/** 「改动」那一包（`usePanelChanges` 的返回）。View 夹具是一串按 View props 写的
 *  Record，这里把它读成解开的那一包：值对不对由 catalog.spec 挂 View 那几格时的 props
 *  校验看着，这里管的是一包里有哪几样、各自是 ref 还是 computed。 */
export function changesBundle(over: Overrides<PanelChangesBundle> = {}): PanelChangesBundle {
  const v = changesPanelProps(over) as Unwrapped<PanelChangesBundle>
  return {
    taskLoadError: ref(v.taskLoadError),
    selectedTask: computed(() => v.selectedTask),
    currentTask: computed(() => v.currentTask),
    sourceStatus: computed(() => v.sourceStatus),
    sourceUnavailable: computed(() => v.sourceUnavailable),
    fileSource: computed(() => v.fileSource),
    fileToolReady: computed(() => v.fileToolReady),
    loading: ref(v.loading),
    refreshing: ref(v.refreshing),
    errorMsg: ref(v.errorMsg),
    noRepo: ref(v.noRepo),
    fileDiffs: computed(() => v.fileDiffs),
    diffByPath: computed(() => v.diffByPath),
    treeFiles: computed(() => v.treeFiles),
    allFiles: ref(v.allFiles),
    openPath: ref(v.openPath),
    fileDraft: ref(v.fileDraft),
    // 上一次读到或存下的正文；没改过就是手上这一份（`fileDirty` 在产品里就是两者不等）。
    fileSaved: ref(v.fileDirty ? '' : v.fileDraft),
    fileSaving: ref(v.fileSaving),
    fileDirty: computed(() => v.fileDirty),
    fileVersion: ref(v.fileVersion),
    fileBinary: ref(v.fileBinary),
    fileTooLarge: ref(v.fileTooLarge),
    fileBytes: ref(v.fileBytes),
    fileReadOnly: computed(() => v.fileReadOnly),
    fileConflict: ref(v.fileConflict),
    openDiff: computed(() => v.openDiff),
    openDiffLines: computed(() => v.openDiffLines),
    effectiveView: computed(() => v.effectiveView),
    fileView: ref(v.fileView),
    openIsImage: computed(() => v.openIsImage),
    openIsDocument: computed(() => v.openIsDocument),
    openDocumentType: computed(() => v.openDocumentType),
    revisionPath: computed(() => v.revisionPath),
    openRawUrl: computed(() => v.openRawUrl),
    collapsedDirs: ref(v.collapsedDirs),
    revealTick: ref(v.revealTick),
    draftCount: computed(() => v.draftCount),
    docBytes: computed(() => v.docBytes),
    docLoading: computed(() => v.docLoading),
    docError: computed(() => v.docError),
    docRendererMissing: computed(() => v.docRendererMissing),
    revs: v.revs,
    canCompare: computed(() => false),
    comparing: ref(false),
    comparison: ref(null),
    comparisonLoading: ref(false),
    comparisonError: ref(''),
    toggleCompare: noopAsync,
    loadAll: noopAsync,
    selectFile: noopAsync,
    closeFile: noop,
    selectVersion: noopAsync,
    // 这一格要回一句「这份文件我这儿有没有」（没有就由工作面板退到项目当前版本）。
    // 预览站里没有真文件，开哪一份都当开到了。
    openFile: async () => true,
    toggleDir: noop,
    downloadOpenFile: noopAsync,
    saveFile: noopAsync,
    overwriteFile: noopAsync,
    fileMerge: ref(null),
    resolveMerge: noop,
    cancelMerge: noop,
    reloadOpenFile: noopAsync,
    onRevisionDecided: noopAsync,
  }
}

/** 预览那一包里由「这一份文件」算出来的几样：照 `usePanelPreview` 的算法算，不由夹具给。 */
type PreviewDerived = 'documentSuffix' | 'documentType' | 'documentName' | 'isImageArtifact' | 'docIdentity' | 'canPage'

/** 预览那一包里写死成取数初值的几样（这一份是 markdown，用不上）：传进来也不会生效。 */
type PreviewFixed = 'frames' | 'navigation' | 'autoReloaded' | 'docPage' | 'docPageHtml'

/** 「预览」那一包（`usePanelPreview` 的返回）。View 夹具里没有的那几样（帧、导航、网页
 *  那一档）给的是取数那一层的初值：这一份是 markdown，用不上它们。后缀、类型、名字、
 *  文档身份从 `previewFile` 算 —— 没有文件时它们就是产品里那时的值，夹具不必另写一遍。 */
export function previewBundle(
  over: Omit<Overrides<PanelPreviewBundle>, PreviewDerived | PreviewFixed> = {}
): PanelPreviewBundle {
  const v = previewPanelProps(over) as Unwrapped<PanelPreviewBundle>
  const file = v.previewFile
  const suffix = suffixOf(file?.path ?? '')
  const identity: DocumentIdentity | null = file
    ? { topicId: DEMO_TOPIC, path: file.path, taskId: null, source: file.source ?? 'live', version: file.version }
    : null
  return {
    frames: computed(() => []),
    displayedFrame: ref(null),
    navigation: ref('idle'),
    navigationError: ref(''),
    frameLoaded: noop,
    frameFailed: noop,
    loading: ref(v.loading),
    refreshing: ref(v.refreshing),
    previewFile: ref(v.previewFile),
    previewMime: ref(v.previewMime),
    previewNamed: ref(v.previewNamed),
    previewUrl: ref(v.previewUrl),
    previewAppNote: ref(v.previewAppNote),
    previewTunnelUp: ref(v.previewTunnelUp),
    previewNamedPath: ref(v.previewNamedPath),
    autoReloaded: ref(false),
    previewError: ref(v.previewError),
    previewReadError: ref(v.previewReadError),
    documentSuffix: computed(() => suffix),
    documentType: computed(() => DOCUMENT_TYPES[suffix] ?? null),
    documentName: computed(() => file?.path.split('/').pop() ?? ''),
    isImageArtifact: computed(() => IMAGE_SUFFIXES.has(suffix)),
    downloadError: ref(v.downloadError),
    docBytes: computed(() => v.docBytes),
    docIdentity: computed(() => identity),
    docSnapshot: ref(null),
    slideContext: computed(() => undefined),
    docLoading: computed(() => v.docLoading),
    docError: computed(() => v.docError),
    docRendererMissing: computed(() => v.docRendererMissing),
    docPage: computed(() => false),
    canPage: computed(() => pageViewOf(identity?.path)),
    docPageHtml: ref(null),
    revs: v.revs,
    editing: ref(v.editing),
    showHistory: ref(v.showHistory),
    editor: v.editor,
    fileHistory: v.fileHistory,
    load: noopAsync,
    downloadArtifact: noopAsync,
    refreshDocument: noop,
    uploadAnnotation: refuseWrite,
    openEditor: noop,
    closeEditor: noop,
    toggleHistory: noop,
    toggleDocPage: noop,
    setPickMode: noop,
  }
}

/** 「文档」那一包（`usePanelDoc` 的返回）：正文是一篇只活在这一页里的协同文档。 */
export function docBundle(
  over: Omit<Overrides<PanelDocBundle>, 'outdated' | 'deleted' | 'documentId'> = {}
): PanelDocBundle {
  const view = docPanelProps()
  const v = {
    session: view.session,
    connection: view.connection,
    peers: view.peers,
    readOnly: view.readOnly,
    editable: view.editable,
    loading: view.loading,
    errorMsg: view.errorMsg as string | null,
    ...over,
  }
  return {
    session: shallowRef(v.session),
    connection: ref(v.connection),
    outdated: ref(false),
    deleted: ref(false),
    renames: ref(0),
    peers: shallowRef(v.peers),
    readOnly: ref(v.readOnly),
    editable: computed(() => v.editable),
    loading: computed(() => v.loading),
    errorMsg: computed(() => v.errorMsg),
    documentId: ref(null),
    suggestionReasons: ref({}),
    fetchSuggestionReasons: noopAsync,
    notifiedMentions: ref([]),
    fetchNotifiedMentions: noop,
    mentionOutside: {
      prompt: ref(null),
      adding: ref(false),
      names: (handles: string[]) => handles.join('、'),
      offer: noop,
      add: noopAsync,
      dismiss: noop,
    },
    commentAuthor: 'wang',
    sendComment: refuseWrite,
    askAgent: noopAsync,
    stopAgent: noopAsync,
    answerToComment: async () => '',
    lastEdit: computed(() => null),
    nameOf: (handle: string) => PANEL_NAMES[handle] ?? handle,
    loadVersions: async () => ({ versions: [], cursor: null }),
    restoreVersion: noopAsync,
    withMentions: (content: string) => content,
    rename: async (title: string) => title,
    currentTitle: async () => null,
    toggleEditable: view.toggleEditable,
    setError: view.setError,
    fetchDocNodes: view.fetchDocNodes,
    imageSrc: view.imageSrc,
    canvas: memoryCanvas(),
    applyEdits: refuseWrite,
  }
}

/** 评论串那一包（`useDocThreads` 的返回）：还没有评论。 */
export function docThreadsBundle(): DocThreadsBundle {
  const view = docPanelProps()
  return { state: reactive(view.threadState), actions: view.threadActions, refresh: noopAsync }
}

/** 名册那一包：真的 `useDocPeople`（它只是几个 computed），名册是验收卡那份。 */
export function docPeopleBundle() {
  return useDocPeople({
    members: () => ACCEPT_REVIEWERS,
    agentHandle: () => 'cheese-demo',
    agentName: () => AGENT_NAME,
  })
}

// ---- 现场 -------------------------------------------------------------------

/** 第 2 步：芝士开了工作区、看了一眼、写了 README、提交推送 —— 一轮还在跑。 */
export const SITE_WORKING = frame(2)
/** 第 3 步：递了验收卡，这一轮收尾。 */
export const SITE_DONE = frame(3)

/** 会话栏那一包（`useSessionInspector` 的返回），停在「收着」。 */
function inspectorBundle(state: AgentControlState | null): SessionInspectorBundle {
  return {
    state: ref(state),
    error: ref(''),
    busy: ref(false),
    expanded: ref(false),
    output: ref(null),
    seat: ref(null),
    seats: computed(() => state?.seats ?? []),
    tasks: computed(() => Object.values(state?.tasks ?? {})),
    mcpServers: ref([]),
    reading: ref<SessionRead>('initialize'),
    values: ref({}),
    fields: computed(() => [] as const),
    readItems: computed(() => [] as { value: SessionRead; title: string }[]),
    fieldLabels: {},
    refresh: noopAsync,
    look: noopAsync,
    setSeat: noop,
    setExpanded: noop,
    setReading: noop,
    setValue: noop,
  }
}

/** 现场那一包（`usePanelSite` 的返回）：手上这一窗就是剧本那一帧的 `site`。 */
export function siteBundle(
  over: { transcript?: Block[]; loading?: boolean; errorMsg?: string | null; connected?: boolean } = {}
): PanelSiteBundle {
  const transcript = over.transcript ?? SITE_WORKING.site
  const agents = [...new Set(transcript.filter((b) => b.meta?.tool).map((b) => b.author))]
  return {
    turnStarts: ref({}),
    scrollRef: ref(null),
    overflowing: ref(new Set<string>()),
    measured: ref(false),
    measureClamp: noop,
    agents: ref(agents),
    viewing: computed(() => null),
    transcript: ref(transcript),
    hasOlder: ref(false),
    loading: ref(over.loading ?? false),
    loadingOlder: ref(false),
    errorMsg: ref(over.errorMsg ?? null),
    onSiteScroll: noop,
    receive: noop,
    selectAgent: noop,
    inspector: inspectorBundle(over.connected ? { id: 'demo-session', connected: true, tasks: {} } : null),
    loadStepOutput: async () => '',
  }
}

/** 整段输出有多少字节（`SiteStepOutput` 的 `bytes` 说的就是这个）。 */
function byteLength(text: string): number {
  return new TextEncoder().encode(text).length
}

/** 一小段输出：`ls` 打印的就是工作区里那几样。 */
const LS_OUTPUT = 'README.md\ndocs\n'
export const LS_OUTPUT_BYTES = byteLength(LS_OUTPUT)
export async function loadLsOutput(): Promise<string> {
  return LS_OUTPUT
}

/** 一大段输出：一次构建把每个产物都报了一行，整段 48 KB 上下。 */
const BUILD_LOG = (() => {
  const lines = ['vite v7.1.4 building for production...', 'transforming...']
  for (let i = 1; byteLength(lines.join('\n')) < 48 * 1024 - 64; i++) {
    const kb = (((i * 37) % 900) + 12) / 10
    lines.push(
      `dist/assets/chunk-${String(i).padStart(4, '0')}.js   ${kb.toFixed(2)} kB │ gzip: ${(kb / 3).toFixed(2)} kB`
    )
  }
  return `${lines.join('\n')}\n✓ built in 41.27s\n`
})()
export const BUILD_LOG_BYTES = byteLength(BUILD_LOG)
/** 后端只留末尾 8 KiB，按 UTF-8 字节数，切断的半个字丢掉（`backend/app/domain/agent/step_output.py`
 *  的 `output_tail`）：取回来的就是那一截。 */
export async function loadBuildTail(): Promise<string> {
  const tail = new TextEncoder().encode(BUILD_LOG).slice(-8 * 1024)
  return new TextDecoder().decode(tail).replace(/^\uFFFD+/, '')
}

// ---- 步骤清单 -----------------------------------------------------------------

/** 剧本第 step 步时那份清单（`todo1` 那条消息上的 `meta.checklist`）。 */
export function checklistAt(step: number): TodoItem[] {
  const checklist = frame(step).chat.find((l) => l.id === 'todo1')?.block?.meta?.checklist
  return typeof checklist === 'object' ? checklist.items : []
}

// ---- 支线 -----------------------------------------------------------------------

/** 剧本里一条消息读成支线的一句（作者、正文、时间）。 */
function replyOf(step: number, who: string, index = 0): ThreadReply {
  const block = frame(step).chat.filter((l) => l.block?.kind === 'message' && l.author === who)[index].block!
  return { author: block.author, content: block.content, created_at: block.created_at }
}

/** 第二条支线下面那一句：剧本里没人在「先记一下」下面回话，照支线的类型造一句，
 *  说话的仍是芝士，时间在那条消息之后。 */
const NOTE_ROOT = replyOf(0, 'wang')
const NOTE_REPLY: ThreadReply = {
  author: 'cheese',
  content: '这件我开个任务整理：第一周的课件放进 docs/week-1.md。',
  created_at: new Date(Date.parse(NOTE_ROOT.created_at) + 5 * 60_000).toISOString(),
}

/** 两条支线：王长鑫点名芝士的那句下面芝士回了话；另一条芝士回了一句，后来转成了任务。 */
export const THREAD_ROWS: ThreadRow[] = [
  {
    id: 'thread-1',
    room_id: DEMO_TOPIC,
    root_block_id: 'root-1',
    reply_count: 2,
    last_reply_at: replyOf(1, 'cheese').created_at,
    last_reply: replyOf(1, 'cheese'),
    participants: ['wang', 'cheese'],
    tasks: [],
    root: replyOf(0, 'wang', 1),
    unread: true,
  },
  {
    id: 'thread-2',
    room_id: DEMO_TOPIC,
    root_block_id: 'root-2',
    reply_count: 1,
    last_reply_at: NOTE_REPLY.created_at,
    last_reply: NOTE_REPLY,
    participants: ['wang', 'cheese'],
    tasks: [{ id: CHANGES_TASK.id, title: CHANGES_TASK.title, status: 'open' }],
    root: NOTE_ROOT,
    unread: false,
  },
]

/** 支线行上的时刻：和话题页的写法一样，今天的写时分，更早的写月日。 */
export function threadTime(iso: string): string {
  const at = new Date(iso)
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : at.toLocaleDateString([], { month: 'numeric', day: 'numeric' })
}

// ---- 例行事务 -------------------------------------------------------------------

function routine(over: Partial<Routine>): Routine {
  return {
    id: 'routine-weekly',
    can_delete: true,
    room_archived: false,
    project_id: DEMO_PROJECT,
    topic_id: DEMO_TOPIC,
    title: '每周一整理课程进展',
    instructions: '把上周这个频道里的作业、提问和改动整理成一页，放进「周报」目录',
    note: '结果按周分文件；只用资料库里的文件',
    trigger: 'schedule',
    trigger_text: '每周周一 09:00（Asia/Shanghai）',
    spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
    timezone: 'Asia/Shanghai',
    state: 'active',
    last_run_status: 'succeeded',
    agent_handle: 'cheese',
    owner_handle: 'wang',
    created_by: 'cheese',
    next_run_at: '2026-10-12T01:00:00Z',
    revision: 1,
    created_at: '2026-09-27T09:00:00Z',
    updated_at: '2026-09-28T01:00:00Z',
    ...over,
  }
}

/** 两条在跑的规则：一条按时间，一条等频道里发生某件事。 */
export const ROUTINE_WEEKLY = routine({})
export const ROUTINE_ACCEPTED = routine({
  id: 'routine-accepted',
  title: '作业被采纳后通知全班',
  instructions: '成果被采纳时，在频道里写一句这次改了什么',
  note: '',
  trigger: 'card_accepted',
  trigger_text: '成果被采纳时',
  spec: { scope: 'room' },
  next_run_at: null,
  // 事件触发的那条还没轮上过：行上画的是「启用中」，不是「没能执行」。
  last_run_status: null,
})

/** 周报那条最近两次执行：一次成了，一次没成。 */
export const ROUTINE_RUNS: Record<string, RoutineRun[]> = {
  [ROUTINE_WEEKLY.id]: [
    {
      id: 'run-2',
      routine_id: ROUTINE_WEEKLY.id,
      trigger_detail: '',
      routine_revision: 1,
      scheduled_for: '2026-10-05T01:00:00Z',
      status: 'succeeded',
      summary: '第一周的周报写好了：两份作业、三个提问',
      outputs: ['周报/2026-10-05.md'],
      error: '',
      created_at: '2026-10-05T01:00:00Z',
      started_at: '2026-10-05T01:00:02Z',
      finished_at: '2026-10-05T01:04:10Z',
    },
    {
      id: 'run-1',
      routine_id: ROUTINE_WEEKLY.id,
      trigger_detail: '',
      routine_revision: 1,
      scheduled_for: '2026-09-28T01:00:00Z',
      status: 'failed',
      summary: '',
      outputs: [],
      error: '工作电脑没有连上',
      created_at: '2026-09-28T01:00:00Z',
      started_at: '2026-09-28T01:00:02Z',
      finished_at: '2026-09-28T01:00:30Z',
    },
  ],
}

/**
 * 「看全部」那一页上的一整页：十条，成的那几次交回一份文件，没能执行的那几次带着原因。
 *
 * 一天一条往回排（第 index 天前），所以行上「今天 / 昨天 / 10月12日」三种写法都在。
 */
export const ROUTINE_RECORDS_RUNS: RoutineRun[] = Array.from({ length: 10 }, (_, index) => {
  const at = new Date(Date.UTC(2026, 9, 14, 10, 2) - index * 86_400_000)
  const missed = index % 3 === 1
  return {
    id: `page-run-${index}`,
    routine_id: ROUTINE_WEEKLY.id,
    trigger_detail: '',
    routine_revision: 1,
    scheduled_for: at.toISOString(),
    status: missed ? 'failed' : 'succeeded',
    summary: missed ? '' : '今天频道里决定的事和还没回音的线索，整理成一页',
    outputs: missed ? [] : [`日报/2026-10-${String(14 - index).padStart(2, '0')}.md`],
    error: missed ? '工作电脑没有连上' : '',
    created_at: at.toISOString(),
    started_at: at.toISOString(),
    finished_at: at.toISOString(),
  }
})

// ---- 现场：一行、组头、队友页签 ------------------------------------------------
//
// 现场拆出来的那几只（一步一行、说的话一行、一轮的组头、顶上按队友筛的那排）在
// `quickstart` 那一帧里凑不齐形状：它那一轮现场只有四次工具调用，没有失败的一步，
// 也没有芝士说的话，更没有第二个队友。`turn` 和 `seats` 两份剧本里都有，所以从
// 它们取，不另手造。

/** `turn` 剧本（「一条消息怎么变成芝士的一轮」）放到第 step 步末尾的那一帧。 */
function turnFrame(step: number) {
  return frameAt(SCENES.turn, step, Number.MAX_SAFE_INTEGER)
}

/** `seats` 剧本（「两个队友在同一个话题里并行」）放到第 step 步末尾的那一帧。 */
function seatsFrame(step: number) {
  return frameAt(SCENES.seats, step, Number.MAX_SAFE_INTEGER)
}

/** 那一段现场里的一步，按工具名找（同一段里工具名不重复）。 */
function actOf(step: number, tool: string): Block {
  const block = turnFrame(step).site.find((b) => b.meta?.tool === tool)
  if (!block) throw new Error(`turn 剧本第 ${step} 步里没有 ${tool} 这一步`)
  return block
}

/** 那一段现场里失败的那一步。 */
function failedActOf(step: number): Block {
  const block = turnFrame(step).site.find((b) => b.meta?.failed === true)
  if (!block) throw new Error(`turn 剧本第 ${step} 步里没有失败的一步`)
  return block
}

/** 那一段现场里芝士说的话，取最先或最后那一句（剧本的 `note` 落成叙述，`meta.progress`）。 */
function sayOf(step: number, which: 'first' | 'last'): Block {
  const says = turnFrame(step).site.filter((b) => b.meta?.progress === true)
  const say = which === 'first' ? says[0] : says[says.length - 1]
  if (!say) throw new Error(`turn 剧本第 ${step} 步里没有芝士说的话`)
  return say
}

/** 一步：改一个文件。 */
export const SITE_ACT_EDIT = actOf(4, 'Edit')
/** 一步：跑一次测试。 */
export const SITE_ACT_BASH = actOf(4, 'Bash')
/** 一步：平台自己做的事（行上的圆点是实心的）。 */
export const SITE_ACT_PLATFORM = actOf(4, 'chat_send')
/** 失败了的一步：`git push` 的结果不知道，那句错跟着它。 */
export const SITE_ACT_FAILED = failedActOf(5)
/** 上面那一步摊开之后的输出：真实的现场里，记下了输出的那一步在 `meta.output_bytes`
 *  上写着有多大，`SiteStepOutput` 收着时说的就是它。 */
export const SITE_ACT_BASH_OUTPUT: Block = {
  ...SITE_ACT_BASH,
  meta: { ...SITE_ACT_BASH.meta, output_bytes: LS_OUTPUT_BYTES },
}

/** 芝士说的话：回一句补充（收到补充：手机上也要检查）。 */
export const SITE_SAY_SHORT = sayOf(4, 'first')
/** 芝士说的话：交代一段经过（推送结果未知那一次）。 */
export const SITE_SAY_LONG = sayOf(5, 'last')
/**
 * 芝士说的一长段：现场里最长的那一种是「做完了交代一遍」—— 改了什么、验证过什么、
 * 还剩什么。剧本里它只说一句话（那一格讲的是「要说话必须 chat_send」），这一种照
 * 同一位、同一件事造：从第二个字起还是真的，行数够越过 12 行那条线。
 */
export const SITE_SAY_DETAIL: Block = {
  ...SITE_SAY_LONG,
  id: 'site-say-detail',
  content: [
    '登录页改完了，两处都动过：',
    '',
    '- 主按钮换成琥珀色实心（`--color-accent-strong`），按下时再深一档；',
    '- 「忘记密码」挪到输入框下面，没登录也点得到。',
    '',
    '验证过这些：',
    '',
    '1. `pnpm exec vitest run src/views/home`：4 个文件、12 条全过；',
    '2. 宽度 390 / 768 / 1280 三档各截了一张，按钮不换行、不压住输入框；',
    '3. Tab 到「忘记密码」有焦点圈，回车跳得过去。',
    '',
    '还没做的：「注册」那一页的按钮还是旧的主色，等设计稿定了再一起换。',
    '',
    '验收卡递了，审阅要点写在里面。',
  ].join('\n'),
}

/** 组头看的那一组：`turn` 剧本到「芝士怎么说话」那一步为止，五步全在同一轮（t1）里。 */
export const SITE_ONE_TURN: SiteTurn<Block> = (() => {
  const turn = groupByTurn(turnFrame(4).site, turnFrame(4).running)[0]
  if (!turn) throw new Error('turn 剧本里没有成组的一轮')
  return turn
})()
/** 那一轮开始的时刻（毫秒）：组头用它和最后一条的时间算「多久」。 */
export const SITE_TURN_START = turnFrame(4).running.t1

/** `seats` 剧本那一帧里做过事的队友，按露面的先后（`usePanelSite` 也是这么攒的）。 */
export const SITE_AGENTS = [...new Set(seatsFrame(1).site.map((b) => b.author))]
/** 页签上写的名字：`seats` 剧本那两页名册（芝士、芝士K）。 */
export const SITE_AGENT_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(SCENES.seats.people).map(([handle, person]) => [handle, person.name])
)
