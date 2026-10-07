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
 *   - 三只壳子（`PanelChanges` / `PanelPreview` / `PanelDoc`）收的是取数那一层的整包，
 *     这里把 View 夹具那一串 props 原样装回 composable 返回的形状：状态是 ref，动作
 *     是什么都不做的函数。壳子只是把包摊开递给 View，所以画出来的就是 View 那几格。
 *   - 剧本没演到的（定时规则、支线、很长的一份 diff），照各自的类型造，人和房间仍然
 *     是剧本里那几位。
 *
 * 单独一份文件：`catalogFixtures.ts` 已经九百多行，再加会顶到一千行的上限。条目在
 * `catalogPanels.ts`。
 */
import type { DocThreadsBundle } from '@/composables/useDocThreads'
import type { PanelChangesBundle } from '@/composables/usePanelChanges'
import type { PanelDocBundle } from '@/composables/usePanelDoc'
import type { PanelPreviewBundle } from '@/composables/usePanelPreview'
import type { PanelSiteBundle } from '@/composables/usePanelSite'
import type { SessionInspectorBundle, SessionRead } from '@/composables/useSessionInspector'
import type { Block, RoomTask, TodoItem, WorkspaceFile } from '@/cx_types'
import type { DiffLine, FileDiff } from '@/lib/diff'
import type { Routine, RoutineRun } from '@/lib/routine'
import type { AgentControlState } from '@/types/agentControl'
import type { ThreadReply, ThreadRow } from '@/types/threads'

import { computed, reactive, ref } from 'vue'

import { useDocPeople } from '@/composables/useDocPeople'

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

const SCENE = SCENES.quickstart

/** 剧本放到第 step 步末尾的那一帧（同 `catalogFixtures.ts` 里那个）。 */
function frame(step: number) {
  return frameAt(SCENE, step, Number.MAX_SAFE_INTEGER)
}

/** 剧本里的人：handle → 名字（王长鑫、芝士）。 */
export const PANEL_NAMES: Record<string, string> = ROOM_REFS.mentionNames

const noop = () => {}
const noopAsync = async () => {}

// ---- 改动那一支活 ----------------------------------------------------------

const CHANGES = changesPanelProps()
const FILE_DIFFS = CHANGES.fileDiffs as FileDiff[]
const DIFF_BY_PATH = CHANGES.diffByPath as Map<string, FileDiff>
const TREE_FILES = CHANGES.treeFiles as WorkspaceFile[]
/** 这一支活本身（「不在当前版本里」那一格列的就是它）。 */
export const CHANGES_TASK = CHANGES.currentTask as RoomTask

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

/** 文件树那几行：和 `PanelChangesView` 一样交给 `buildFileRows` 折。 */
export function fileTreeRows(showAll: boolean, expanded: string[] = []) {
  return buildFileRows({ files: TREE_FILES, diffByPath: DIFF_BY_PATH, showAll, expandedDirs: new Set(expanded) })
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

// ---- 三只壳子收的那几包 ------------------------------------------------------

/** 去掉几样：壳子自己的 props 不在取数那一包里。 */
function without(values: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([key]) => !keys.includes(key)))
}

/** 一串 View 的 props 装回 composable 的形状：状态各是一个 ref，`keep` 里那几样本来就
 *  是一整包（修订、编辑器、历史），原样放进去。 */
function refsOf(values: Record<string, unknown>, keep: string[]): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, keep.includes(key) ? value : ref(value)])
  )
}

/** 「改动」那一包（`usePanelChanges` 的返回）。 */
export function changesBundle(over: Record<string, unknown> = {}): PanelChangesBundle {
  const state = without(changesPanelProps(over), ['topicId', 'readOnly'])
  return {
    ...refsOf(state, ['revs']),
    fileSaved: ref(false),
    loadAll: noopAsync,
    selectFile: noopAsync,
    selectVersion: noopAsync,
    openFile: noopAsync,
    toggleDir: noop,
    downloadOpenFile: noopAsync,
    saveFile: noopAsync,
    overwriteFile: noopAsync,
    reloadOpenFile: noopAsync,
    onRevisionDecided: noop,
  } as unknown as PanelChangesBundle
}

/** 「预览」那一包（`usePanelPreview` 的返回）。View 夹具里没有的那几样（帧、导航、
 *  文档身份、网页那一档）给的是取数那一层的初值：这一份是 markdown，用不上它们。 */
export function previewBundle(over: Record<string, unknown> = {}): PanelPreviewBundle {
  const state = without(previewPanelProps(over), ['topicId', 'projectId', 'frameName'])
  return {
    ...refsOf(state, ['revs', 'editor', 'fileHistory']),
    frames: computed(() => []),
    displayedFrame: ref(null),
    navigation: ref('idle'),
    navigationError: ref(''),
    autoReloaded: ref(false),
    docIdentity: ref(null),
    docSnapshot: ref(null),
    slideContext: ref(undefined),
    docPage: ref(false),
    canPage: ref(false),
    docPageHtml: ref(null),
    frameLoaded: noop,
    frameFailed: noop,
    load: noopAsync,
    downloadArtifact: noopAsync,
    refreshDocument: noopAsync,
    uploadAnnotation: async () => '',
    openEditor: noop,
    closeEditor: noop,
    toggleHistory: noop,
    toggleDocPage: noop,
    setPickMode: noop,
  } as unknown as PanelPreviewBundle
}

/** 「文档」那一包（`usePanelDoc` 的返回）：正文是一篇只活在这一页里的协同文档。 */
export function docBundle(over: Record<string, unknown> = {}): PanelDocBundle {
  const view = docPanelProps(over)
  return {
    session: ref(view.session),
    connection: ref(view.connection),
    outdated: ref(false),
    deleted: ref(false),
    renames: ref(0),
    peers: ref(view.peers),
    readOnly: ref(view.readOnly),
    editable: ref(view.editable),
    loading: ref(view.loading),
    errorMsg: ref(view.errorMsg),
    documentId: ref(null),
    suggestionReasons: ref({}),
    fetchSuggestionReasons: noop,
    commentAuthor: 'wang',
    sendComment: async () => ({}),
    askAgent: noopAsync,
    stopAgent: noopAsync,
    answerToComment: async () => '',
    lastEdit: ref(null),
    nameOf: (handle: string) => PANEL_NAMES[handle] ?? handle,
    loadVersions: async () => ({ versions: [] }),
    restoreVersion: noopAsync,
    withMentions: (content: string) => content,
    rename: async (title: string) => title,
    currentTitle: async () => null,
    toggleEditable: view.toggleEditable,
    setError: view.setError,
    fetchDocNodes: view.fetchDocNodes,
    imageSrc: view.imageSrc,
    applyEdits: noopAsync,
  } as unknown as PanelDocBundle
}

/** 评论串那一包（`useDocThreads` 的返回）：还没有评论。 */
export function docThreadsBundle(): DocThreadsBundle {
  const view = docPanelProps()
  return { state: reactive(view.threadState), actions: view.threadActions, refresh: noopAsync } as DocThreadsBundle
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
  } as unknown as PanelSiteBundle
}

/** 摊开一步之后那一段输出：`ls` 打印的就是工作区里那几样。 */
export async function loadLsOutput(): Promise<string> {
  return 'README.md\ndocs\n'
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

/** 两条支线：王长鑫点名芝士的那句下面芝士回了话；另一条后来转成了任务。 */
export const THREAD_ROWS: ThreadRow[] = [
  {
    id: 'thread-1',
    room_id: DEMO_TOPIC,
    root_block_id: 'root-1',
    reply_count: 2,
    last_reply_at: replyOf(1, 'cheese').created_at,
    last_reply: replyOf(1, 'cheese'),
    participants: ['wang', 'cheese'],
    task: null,
    root: replyOf(0, 'wang', 1),
    unread: true,
  },
  {
    id: 'thread-2',
    room_id: DEMO_TOPIC,
    root_block_id: 'root-2',
    reply_count: 1,
    last_reply_at: replyOf(0, 'wang').created_at,
    last_reply: replyOf(0, 'wang'),
    participants: ['wang'],
    task: { id: CHANGES_TASK.id, title: CHANGES_TASK.title, status: 'open' },
    root: replyOf(0, 'wang'),
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

// ---- 定时与触发 -------------------------------------------------------------------

function routine(over: Partial<Routine>): Routine {
  return {
    id: 'routine-weekly',
    can_manage: true,
    room_archived: false,
    project_id: DEMO_PROJECT,
    topic_id: DEMO_TOPIC,
    title: '每周一整理课程进展',
    instructions: '把上周这个频道里的作业、提问和改动整理成一页，放进「周报」目录',
    context_scope: '',
    output_dir: '周报',
    trigger: 'schedule',
    trigger_text: '每周周一 09:00（Asia/Shanghai）',
    spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
    timezone: 'Asia/Shanghai',
    state: 'active',
    agent_handle: 'cheese',
    owner_handle: 'wang',
    proposed_by: 'cheese',
    confirmed_by: 'wang',
    confirmed_at: '2026-09-28T01:00:00Z',
    next_run_at: '2026-10-12T01:00:00Z',
    revision: 1,
    created_at: '2026-09-27T09:00:00Z',
    updated_at: '2026-09-28T01:00:00Z',
    ...over,
  }
}

/** 一条在跑的周报，和芝士刚起草、等人确认的一条。 */
export const ROUTINE_WEEKLY = routine({})
export const ROUTINE_DRAFT = routine({
  id: 'routine-accepted',
  title: '作业被采纳后通知全班',
  instructions: '成果被采纳时，在频道里写一句这次改了什么',
  output_dir: '',
  trigger: 'card_accepted',
  trigger_text: '成果被采纳时',
  spec: { scope: 'room' },
  state: 'draft',
  confirmed_by: null,
  confirmed_at: null,
  next_run_at: null,
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
