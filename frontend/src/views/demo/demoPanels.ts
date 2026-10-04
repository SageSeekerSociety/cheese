// 演示房间右侧那几格的数据：剧本里写的东西（`demoScene` 算出来的帧）→ 产品组件要
// 的形状 → 演示后端的那几条路由。
//
// 那几格用的是产品里真的面板（`PanelOverview` / `PanelChanges` / `PanelPreview`），
// 它们不接 props 拿数据，而是自己去 `/api/...` 取。演示页没有后端，所以这里把剧本
// 声明的那点东西翻译成后端会回的信封，交给 `demoBackend.answer`。产品组件一行不改，
// 演示和真界面画的于是是同一段代码——「产品一改、演示跟着变」这句在这里才成立。
//
// 剧本只写要讲的东西（哪几个文件、改哪几行、文档正文），信封里那些和这一步无关的
// 字段（版本号、字节数、时间戳）由这里补。
import type { RoomOutput } from '@/api'
import type {
  Block,
  BoardPhrase,
  FileContent,
  PreviewInfo,
  RoomTask,
  TodoItem,
  TopicProgress,
  WorkspaceFile,
} from '@/cx_types'
import type { ChangesScene, Frame, OverviewScene, PreviewScene, SceneTask } from './demoScene'

import { answer } from './demoBackend'

/** 演示房间的那个话题，和 DemoRoom 里那句 `{ id: DEMO_TOPIC }` 是同一个。 */
export const DEMO_TOPIC = 'demo'
export const DEMO_DOCUMENT = 'demo-doc'
/** 它挂在的那个项目。改动那一格按项目取 diff，所以要有一个。 */
export const DEMO_PROJECT = 'demo'

const HOUR = 3600_000
const MINUTE = 60_000

const COLUMN_PHRASE: Record<NonNullable<SceneTask['column']>, BoardPhrase> = {
  building: 'running',
  delivering: 'awaiting_checks',
  needs_you: 'awaiting_review',
  done: 'accepted',
}

/** 相对此刻的 ISO 时刻：剧本里的活写「多久以前」，画出来才是「刚刚 / 3 小时前」。 */
function since(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString()
}

// ---- 总览 ----

/** 一件活：剧本里只写要讲的那几项，其余按一件普通的、开着分支的活补齐。 */
export function roomTask(task: SceneTask, index: number): RoomTask & { blocks: Block[] } {
  const column = task.column ?? 'building'
  return {
    id: task.id ?? `demo-task-${index + 1}`,
    project_id: DEMO_PROJECT,
    room_id: DEMO_TOPIC,
    title: task.title,
    status: column === 'done' ? 'closed' : 'open',
    owner_handle: task.who ?? 'cheese',
    reviewer_handle: 'wang',
    created_by: 'wang',
    // 有分支才是有改动可看的那一条（`PanelChanges` 按它过滤）。
    branch_name: `cheese/demo-${index + 1}`,
    brief: '',
    conclusion: null,
    base_branch: 'main',
    created_at: since(HOUR * (2 + index)),
    updated_at: since(MINUTE * (5 + index)),
    presentation: { column, phrase: task.phrase ?? COLUMN_PHRASE[column] },
    card: null,
    blocks: [],
  }
}

export function progressOf(overview: OverviewScene | null): TopicProgress {
  const items: TodoItem[] = (overview?.progress ?? []).map((it, i) => ({
    id: `${i + 1}`,
    subject: it.subject,
    status: it.status,
  }))
  return { items, updated_at: items.length ? since(3 * MINUTE) : null }
}

// ---- 改动 ----

/** 剧本里那几行 diff 拼成一份 git 输出。
 *
 *  文件头、hunk 头由这里算：剧本里抄那些只是噪音，而且抄错（行数和 `+` `-` 对不上）
 *  不会报错，只会让树上的 +N −M 和点开看到的那一段不是一回事。 */
export function diffOf(changes: ChangesScene): string {
  return changes.files
    .map((file) => {
      const status = file.status ?? 'modified'
      const lines = file.diff
      const removed = lines.filter((l) => l.startsWith('-')).length
      const added = lines.filter((l) => l.startsWith('+')).length
      const context = lines.filter((l) => !l.startsWith('+') && !l.startsWith('-')).length
      const head = [`diff --git a/${file.path} b/${file.path}`]
      if (status === 'added') head.push('new file mode 100644')
      if (status === 'removed') head.push('deleted file mode 100644')
      head.push(`index ${'0'.repeat(7)}..${'1'.repeat(7)} 100644`)
      head.push(status === 'added' ? '--- /dev/null' : `--- a/${file.path}`)
      head.push(status === 'removed' ? '+++ /dev/null' : `+++ b/${file.path}`)
      const before = context + removed
      const after = context + added
      head.push(
        status === 'added'
          ? `@@ -0,0 +1,${after} @@`
          : status === 'removed'
            ? `@@ -1,${before} +0,0 @@`
            : `@@ -1,${before} +1,${after} @@`
      )
      return [...head, ...lines].join('\n')
    })
    .join('\n')
}

/** 改动里提到的那几份文件，做成工作区列表（树上那个字节数读它）。 */
export function filesOf(changes: ChangesScene): WorkspaceFile[] {
  return changes.files.map((f) => ({ path: f.path, bytes: f.diff.join('\n').length + 128 }))
}

/** 改动那一格里，读者点开的那一份（演示不点，但面板会自己打开第一份）。 */
export function fileOf(changes: ChangesScene | null, path: string): FileContent {
  const file = changes?.files.find((f) => f.path === path)
  const content = file
    ? file.diff
        .filter((l) => !l.startsWith('-'))
        .map((l) => l.replace(/^\+/, ''))
        .join('\n')
    : ''
  return { path, content, version: '1', bytes: content.length, binary: false, too_large: false, source: 'live' }
}

// ---- 预览 ----

export function previewInfoOf(preview: PreviewScene | null): PreviewInfo | null {
  if (!preview) return null
  return {
    kind: 'file',
    path: preview.path,
    mime: preview.mime ?? 'text/markdown',
    url: null,
    artifact_id: 'demo-artifact',
    version: '1',
  }
}

export function previewContentOf(preview: PreviewScene | null): FileContent {
  const path = preview?.path ?? ''
  const content = preview?.content ?? ''
  return { path, content, version: '1', bytes: content.length, binary: false, too_large: false, source: 'live' }
}

// ---- 装到演示后端上 ----
/** 这一帧右侧那几格该读到的每一条。面板一挂上就会来取，所以这要在它之前跑。 */
export function installPanelAnswers(frame: Frame): void {
  const overview = frame.overview
  const changes = frame.changes
  const tasks = (overview?.tasks ?? []).map(roomTask)
  // 房间里摆出来的东西：改动那一格列的是文件，预览那一格看的也是房间里的一份。
  const shown: RoomOutput[] = frame.preview
    ? [
        {
          path: frame.preview.path,
          mime: frame.preview.mime ?? 'text/markdown',
          kind: 'file',
          shown_at: since(2 * MINUTE),
        },
      ]
    : []

  answer(`/topics/${DEMO_TOPIC}/document`, () => ({ id: DEMO_DOCUMENT }))
  answer(`/documents/${DEMO_DOCUMENT}/nodes`, () => ({ data: [], total: 0 }))
  answer(`/documents/${DEMO_DOCUMENT}/comments/threads`, () => ({ data: [], total: 0 }))
  answer(`/topics/${DEMO_TOPIC}/progress`, () => progressOf(overview))
  answer(`/topics/${DEMO_TOPIC}/tasks`, () => ({ data: tasks, total: tasks.length }))

  answer(`/projects/${DEMO_PROJECT}/forge`, () => ({
    kind: 'github_app',
    connected: true,
    repo: 'demo/repo',
    url: null,
  }))
  answer(`/projects/${DEMO_PROJECT}/git/diff`, () => ({ diff: changes ? diffOf(changes) : '' }))
  answer(`/projects/${DEMO_PROJECT}/git/log`, () => ({ data: [], total: 0 }))
  answer(`/projects/${DEMO_PROJECT}/files`, () => ({
    data: changes ? filesOf(changes) : [],
    total: changes ? changes.files.length : 0,
    source: 'live',
  }))
  answer(`/projects/${DEMO_PROJECT}/file`, () => fileOf(changes, (changes?.files[0]?.path ?? '') as string))

  answer(`/topics/${DEMO_TOPIC}/preview`, () => previewInfoOf(frame.preview))
  answer(`/topics/${DEMO_TOPIC}/preview/file`, () => previewContentOf(frame.preview))
  // 预览那一格底下「这个房间里摆出来的东西」那几行读的就是它。
  answer(`/topics/${DEMO_TOPIC}/shown`, () => ({ data: shown, total: shown.length }))
}
