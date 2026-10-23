/**
 * 预览站「工作区整页」那一组（`catalogScenesWorkspace.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着各组件自己的 `.spec.ts` 和类型造：一条规则抄
 * `lib/routine.ts` 的 `Routine`（`ProjectRoutinesView.spec.ts` 里的 `routine()` 就是
 * 这个形状），一条已归档项目抄 `MyArchivedProjectsView.spec.ts` 里的 `archived()`，
 * 设备审批的回包是 `cx_types` 的 `DeviceApproval`。搜索结果行不手打：一份真的
 * `ProjectSearchHits` 交给五个数据源自己的 `itemsOf`，出来的就是产品里那一行。
 *
 * 这里只出数据（外加几个把参数拼成 props 的小函数），不引运行时要装的东西：哪一份配
 * 哪个组件、每一格看什么，在 `catalogScenesWorkspace.ts`。
 */
import type { ProjectSearchHits } from '@/api/projectSearch'
import type { PaletteItem } from '@/commands/palette/sources'
import type { DeviceApproval, Project, Topic } from '@/cx_types'
import type { Routine, RoutineRun } from '@/lib/routine'
import type { ContentKind } from '@/views/workspace/search/projectSearch'

import { demoRouter } from './demoRouter'

import { docs } from '@/views/workspace/search/docs.palette'
import { library } from '@/views/workspace/search/library.palette'
import { messages } from '@/views/workspace/search/messages.palette'
import { projectDocs } from '@/views/workspace/search/projectDocs.palette'
import { tasks } from '@/views/workspace/search/tasks.palette'

// ---- 例行事务（ProjectRoutinesViewView）----------------------------------

/** 一条例行事务：后端 `GET /routines/{id}` 那个形状。`state` 是启用中还是已暂停。 */
export function routine(over: Partial<Routine> = {}): Routine {
  return {
    id: 'routine-weekly',
    can_delete: true,
    room_archived: false,
    project_id: 'p-demo',
    topic_id: 'topic-weekly',
    title: '每周一整理上周进展',
    instructions: '把上周房间里的讨论和任务收成一页周报。',
    note: '结果放在 reports/；只用资料库里的文件',
    trigger: 'schedule',
    trigger_text: '每周一 09:00',
    spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
    timezone: 'Asia/Shanghai',
    state: 'active',
    last_run_status: 'succeeded',
    credits_this_month: 128.4,
    agent_handle: 'cheese-demo',
    owner_handle: 'cheese',
    created_by: 'cheese',
    next_run_at: '2026-10-19T01:00:00Z',
    revision: 1,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T01:00:00Z',
    ...over,
  }
}

/** 一次执行记录：展开某一条规则时，它下面那一串里的一个。 */
export function routineRun(over: Partial<RoutineRun> = {}): RoutineRun {
  return {
    id: 'run-1',
    routine_id: 'routine-weekly',
    trigger_detail: '每周一 09:00',
    routine_revision: 1,
    scheduled_for: '2026-10-06T01:00:00Z',
    status: 'succeeded',
    summary: '整理好了 12 条讨论，结果放在 reports/2026-10-06.md。',
    outputs: ['reports/2026-10-06.md'],
    error: '',
    created_at: '2026-10-06T01:00:00Z',
    started_at: '2026-10-06T01:00:05Z',
    finished_at: '2026-10-06T01:03:00Z',
    ...over,
  }
}

/** 一个房间：新建例行事务时要选一个，行里也写它叫什么。 */
export function topic(over: Partial<Topic> = {}): Topic {
  return {
    id: 'topic-weekly',
    project_id: 'p-demo',
    parent_id: null,
    title: '周会',
    kind: 'topic',
    status: 'open',
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  }
}

/** 三条规则：一条按时间跑，一条上一次没能执行，一条暂停着 —— 行上那三颗图形各来一颗。 */
export const ROUTINES: Routine[] = [
  routine({ id: 'routine-daily', title: '每天提醒作业截止', trigger_text: '每天 18:00' }),
  routine({ id: 'routine-missed', last_run_status: 'failed' }),
  routine({ id: 'routine-paused', state: 'paused', last_run_status: null, next_run_at: null }),
]

/** 键是规则 id：展开某一条时它下面那一串。 */
export const ROUTINE_RUNS: Record<string, RoutineRun[]> = {
  'routine-weekly': [
    routineRun(),
    routineRun({
      id: 'run-0',
      scheduled_for: '2026-09-29T01:00:00Z',
      status: 'failed',
      summary: '',
      error: '没有取到上周的房间内容',
      created_at: '2026-09-29T01:00:00Z',
      started_at: '2026-09-29T01:00:05Z',
      finished_at: '2026-09-29T01:00:20Z',
    }),
  ],
}

/** 规则里点名的人名 → 显示名。名册查询在页面里，这一串只给结果。 */
export const USER_NAMES: Record<string, string> = { cheese: '芝士', 'li-laoshi': '李老师' }

// ---- 已归档的项目（MyArchivedProjectsViewView）-----------------------------

/** 一个我归档了的项目：`MyArchivedProjectsView.spec.ts` 里的 `archived()`。 */
export function archivedProject(over: Partial<Project> = {}): Project {
  return {
    id: 'p-grad',
    name: '毕业设计',
    created_at: '2026-09-01T00:00:00Z',
    archived_at: '2026-09-28T00:00:00Z',
    ...over,
  }
}

// ---- 设备审批（ConnectViewView）--------------------------------------------

/** 一次被批准（或已被批准过）的设备：后端 `POST /devices/connect` 的回包。 */
export function deviceApproval(over: Partial<DeviceApproval> = {}): DeviceApproval {
  return { device_id: 'dev-1', device_name: 'andy-macbook', team_id: 1, ...over }
}

// ---- 搜索结果（ProjectSearchViewView）--------------------------------------

/** 搜索的那五栏，和容器传下去的是同一份（`views/workspace/search/*.palette.ts`）。 */
export const CONTENT_KINDS: ContentKind[] = [messages, tasks, docs, projectDocs, library]

/** 一次搜索的回包：两条消息、一个任务、一份资料库文件。 */
export const SEARCH_HITS: ProjectSearchHits = {
  records: [
    {
      id: '1',
      room_id: 'topic-weekly',
      room_title: '周会',
      kind: 'message',
      author: 'li-laoshi',
      author_name: '李老师',
      author_name_source: null,
      created_at: '2026-10-09T02:00:00Z',
      task_id: null,
      snippet: '下周三的课改到线上，有课件的提前发我',
    },
    {
      id: '2',
      room_id: 'topic-weekly',
      room_title: '周会',
      kind: 'message',
      author: 'cheese',
      author_name: '芝士',
      author_name_source: 'default',
      created_at: '2026-10-08T02:00:00Z',
      task_id: null,
      snippet: '课件上传到资料库了，文件名带日期',
    },
  ],
  tasks: [
    {
      id: 't1',
      room_id: 'topic-weekly',
      title: '整理上周进展',
      title_source: 'human',
      status: 'open',
      room_title: '周会',
      snippet: '',
    },
  ],
  library: [{ path: 'reports/2026-10-06.md', bytes: 3210, modified: '2026-10-06T01:00:00Z' }],
}

/** 别处没有这些字的一次搜索：只命中「资料库」那一栏。 */
export const SEARCH_HITS_LIBRARY_ONLY: ProjectSearchHits = {
  records: [],
  tasks: [],
  library: [{ path: 'reports/2026-09-29.md', bytes: 2880, modified: '2026-09-29T01:00:00Z' }],
}

/** 每类各有多少（后端 `with_counts`）。键是后端 `only` 认的那几个名字。 */
export const SEARCH_COUNTS: Record<string, number> = {
  message: 12,
  doc: 4,
  doc_node: 1,
  comment: 2,
  weekly: 0,
  tasks: 3,
  library: 1,
}

const ROUTER = demoRouter()

/** 一栏结果：用这一栏自己的数据源把 hits 变成行（`PaletteItem`）。 */
export function searchSection(kind: ContentKind, hits: ProjectSearchHits): { kind: ContentKind; items: PaletteItem[] } {
  return { kind, items: kind.itemsOf(hits, 'p-demo', ROUTER) }
}

/** 「全部」那一栏：每类各取前几条，空的那几类不留（和容器一样）。 */
export function allSections(hits: ProjectSearchHits = SEARCH_HITS): { kind: ContentKind; items: PaletteItem[] }[] {
  return CONTENT_KINDS.map((kind) => searchSection(kind, hits)).filter((group) => group.items.length)
}
