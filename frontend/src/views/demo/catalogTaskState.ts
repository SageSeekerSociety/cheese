/**
 * 「任务状态」：同一件任务的每一种状态，在每一处画它的地方长什么样。
 *
 * 状态的记号（图标、颜色、动不动）和它下面那一行只有一份（`lib/taskState.ts`，画它的是
 * `TaskStateMark`），频道里的任务卡、全部任务、等你处理、任务页头、侧栏的任务行都用它。
 * 这一册把十种情形在五处各摆一遍，好一眼对出「同一件事在几处是不是同一个样子」：
 *
 *   在做（会话在跑，转）、在做（会话停着，不转）、在等、卡住（留了话）、卡住（没留话）、
 *   待回答、等审、退回、开启、关闭
 *
 * 等你处理只列下一步在人手上的那几种（卡住、待回答、等审、退回、检查没过），在做、在等、
 * 开启、关闭本来就不会出现在那里。
 *
 * 单独一册，和 `catalogRoom.ts`、`catalogRail.ts` 同一个理由：那几册已经顶到一千行的上限。
 */
import type { BoardColumn, BoardPhrase, RoomTask } from '@/cx_types'
import type { TaskLine } from '@/lib/channelTasks'
import type { WaitingWork } from '@/types/waiting'
import type { CatalogEntry, CatalogNeed, CatalogState } from './catalog'

import { AGENT_NAME } from './catalogFixtures'
import { CATALOG_NAMES, catalogTaskState, TASK_HEADER_PROPS } from './catalogRoom'

import WaitingList from '@/components/common/Notification/WaitingList.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import TopicRailTaskRow from '@/components/topic-sidebar/TopicRailTaskRow.vue'
import { t } from '@/i18n'
import ProjectTasksView from '@/views/workspace/ProjectTasksView.vue'

const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

const NOW = Date.now()
const MIN = 60_000
const ago = (ms: number) => new Date(NOW - ms).toISOString()

/** 一种情形：它在看板上是哪一格，任务上带着哪几样事实。 */
interface Case {
  /** 这一格的名字。 */
  name: string
  note: string
  column: BoardColumn
  phrase: BoardPhrase
  /** 任务上跟这一格有关的那几样。 */
  over: Partial<RoomTask>
  title: string
}

const CASES: Case[] = [
  {
    name: '在做（会话在跑）',
    note: '绿色，图标慢慢转：AI 队友这一刻真的在跑。下面一行是它报的那句和已经做了多久。',
    column: 'building',
    phrase: 'running',
    over: {
      session_running: true,
      moving: true,
      progress_state: 'working',
      progress_note: '在改登录表单，加「记住我」那个勾选框',
      progress_set_at: ago(12 * MIN),
    },
    title: '登录页加「记住我」',
  },
  {
    name: '在做（会话停着）',
    note: '还是绿色，但换成一个不转的图标：它说过在做，这一刻没有在跑。关掉动画后两种也分得开。',
    column: 'building',
    phrase: 'running',
    over: {
      session_running: false,
      moving: false,
      progress_state: 'working',
      progress_note: '首页的轮播图换成静态的三张',
      progress_set_at: ago(40 * MIN),
    },
    title: '首页轮播换成静态图',
  },
  {
    name: '在等',
    note: '中性灰，沙漏：在等构建或检查的结果。下面一行是在等什么、已经等了多久。',
    column: 'building',
    phrase: 'waiting',
    over: { progress_note: '等 CI 跑完再看失败的两个用例', progress_set_at: ago(25 * MIN) },
    title: '修复注册页的两个用例',
  },
  {
    name: '卡住（留了话）',
    note: '警示色，叹号；第一次出现时闪一下，之后不再闪。下面一行是它要你做的事。',
    column: 'needs_you',
    phrase: 'blocked',
    over: { progress_note: '没有预发环境的测试账号，登不进去验证', progress_set_at: ago(8 * MIN) },
    title: '预发环境验证登录',
  },
  {
    name: '卡住（没留话）',
    note: '它没说卡在哪：下面一行让你打开看看它需要什么，叫的是做它的那位队友的名字。',
    column: 'needs_you',
    phrase: 'blocked',
    over: { progress_note: null, progress_set_at: ago(3 * MIN) },
    title: '导出成绩单',
  },
  {
    name: '待回答',
    note: '警示色，问号，和卡住一样第一次出现时闪一下。下面一行说在等谁回答。',
    column: 'needs_you',
    phrase: 'awaiting_answer',
    over: { waiting_on: 'alice', awaits_me: true },
    title: '作业截止时间怎么定',
  },
  {
    name: '待审阅',
    note: '强调色，审阅图标。下面一行是在等谁（是你就写「等你」）、被审阅的是什么、第几版、什么时候提交的。',
    column: 'needs_you',
    phrase: 'awaiting_review',
    over: {
      waiting_on: 'bobby',
      reviewer_handle: 'bobby',
      card: {
        id: 'card-2',
        status: 'pending',
        since: ago(3 * 60 * MIN),
        version: 2,
        subject: '登录表单加「记住我」',
        reviewer: 'bobby',
      },
    },
    title: '作业 3 的参考答案',
  },
  {
    name: '退回',
    note: '危险色，回退的箭头：交付被退回了，还等着队友去改。不动。',
    column: 'needs_you',
    phrase: 'bounced',
    over: {},
    title: '课程网站的导航栏',
  },
  {
    name: '开启',
    note: '淡色空心圈：开着，还没有人报在做什么。没有第二行。',
    column: 'building',
    phrase: 'started',
    over: {},
    title: '换一门课的题目板',
  },
  {
    name: '已采纳',
    note: '最后一步被采纳：绿色的勾，字是中性的。没交付就关掉的是淡色的一道横，写「已关闭」。没有第二行。',
    column: 'done',
    phrase: 'accepted',
    over: { status: 'closed', closed_at: ago(60 * MIN) },
    title: '把实验指导书发下去',
  },
]

function caseTask(c: Case, i: number): RoomTask {
  return {
    id: `task-state-${i}`,
    project_id: 'demo',
    room_id: 'demo-room',
    title: c.title,
    title_source: 'human',
    status: 'open',
    owner_handle: 'alice',
    contributor_handles: [],
    created_at: ago(3 * 24 * 60 * MIN),
    updated_at: ago(30 * MIN),
    last_activity_at: ago(30 * MIN),
    awaits_me: false,
    stalled: false,
    presentation: { column: c.column, phrase: c.phrase },
    ...c.over,
  }
}

const TASKS = CASES.map(caseTask)
const stateOf = (task: RoomTask) => catalogTaskState(task.presentation.phrase, task)

/** 每种情形各一格：`build` 给这一格的 props，`expect` 是这一格才有的那句。 */
function perCase(
  build: (task: RoomTask, c: Case) => Record<string, unknown>,
  expect: (task: RoomTask, c: Case) => string
): CatalogState[] {
  return CASES.map((c, i) => ({
    name: c.name,
    note: c.note,
    props: build(TASKS[i], c),
    expect: expect(TASKS[i], c),
    ...(c.over.session_running ? { expectSelector: '[data-live]' } : {}),
  }))
}

function cardLine(task: RoomTask): TaskLine {
  return {
    id: task.id,
    title: task.title,
    owner: task.owner_handle ?? null,
    creator: task.owner_handle ?? null,
    state: stateOf(task),
    mine: false,
    accepted: null,
    at: task.updated_at,
    latest: '报名表的字段已经定下来，接着写填表说明',
  }
}

/** 等你处理里的一行：任务停在某一格上，点到你的原因，和后端写的第二行。 */
function waitingRow(
  i: number,
  phrase: WaitingWork['phrase'],
  reason: WaitingWork['reason'],
  detail: string,
  title: string
): WaitingWork {
  return {
    kind: 'work',
    projectId: 'demo',
    projectName: '课程平台',
    topicId: 'demo-room',
    topicTitle: '前端',
    taskId: `waiting-${i}`,
    taskTitle: title,
    phrase,
    reason,
    detail,
    at: ago((i + 1) * 10 * MIN),
  }
}

export const TASK_STATE_ENTRIES: CatalogEntry[] = [
  {
    id: 'task-state-mark',
    title: 'TaskStateMark',
    about:
      '任务状态的记号：图标 + 那一句。哪种状态、什么图标、什么颜色、动不动都在 lib/taskState.ts 里定，五处画任务状态的地方都用它。',
    file: 'src/components/task/TaskStateMark.vue',
    component: TaskStateMark,
    needs: ['vuetify'],
    states: perCase(
      (task) => ({ state: stateOf(task) }),
      (task) => stateOf(task).label
    ),
  },
  {
    id: 'task-state-card',
    title: 'TaskCard · 任务状态',
    about: '频道主线上的任务卡，每一种状态各一张：右边是状态记号，第二行是状态下面那一句。',
    file: 'src/components/room/TaskCard.vue',
    component: TaskCard,
    needs: UI_T,
    states: perCase(
      (task) => ({ task: cardLine(task), ownerName: 'Alice' }),
      (task) => task.title
    ),
  },
  {
    id: 'task-state-header',
    title: 'TaskHeader · 任务状态',
    about: '任务页的页头，每一种状态各一格：任务名后面是状态记号和它下面那一句。',
    file: 'src/components/task/TaskHeader.vue',
    component: TaskHeader,
    needs: UI_T,
    states: perCase(
      (task) => ({ ...TASK_HEADER_PROPS, task, memberNames: CATALOG_NAMES }),
      (task) => task.title
    ),
  },
  {
    id: 'task-state-rail',
    title: 'TopicRailTaskRow · 任务状态',
    about: '侧栏里挂在频道下面的任务行，每一种状态各一行：行首是状态图标，标题下面是那一句。',
    file: 'src/components/topic-sidebar/TopicRailTaskRow.vue',
    component: TopicRailTaskRow,
    needs: ['vuetify'],
    states: perCase(
      (task) => ({ task, selected: false, state: stateOf(task) }),
      (task) => task.title
    ),
  },
  {
    id: 'task-state-board',
    title: 'ProjectTasksView · 任务状态',
    about: '全部任务：十种情形摆在一页里，按「下一步在谁手上」分组，每行的状态一列是记号，标题下面是那一句。',
    file: 'src/views/workspace/ProjectTasksView.vue',
    component: ProjectTasksView,
    needs: ['vuetify', 'i18n', 'router'],
    args: {
      channels: [{ id: 'demo-room', title: '前端', glyph: 'mdi-pound' }],
      channelId: null,
      names: CATALOG_NAMES,
      agentName: AGENT_NAME,
      avatars: {},
      me: 'alice',
      loading: false,
      failed: false,
      closedCounts: null,
      closedHasMore: false,
      closedLoading: false,
      closedFailed: false,
      delivery: 'all',
      whose: 'all',
    },
    states: [
      {
        name: '每种状态各一行',
        note: '进行中的九件按列分组；最后一件已关闭的在「已关闭」里（筛选选了「全部」）。',
        props: {
          tasks: TASKS.filter((task) => task.status === 'open'),
          closedTasks: TASKS.filter((task) => task.status === 'closed'),
          status: 'all',
        },
        expect: '修复注册页的两个用例',
        expectSelector: '[data-live]',
      },
    ],
  },
  {
    id: 'task-state-waiting',
    title: 'WaitingList · 任务状态',
    about: '等你处理：下一步在你手上的那几种状态，行首是状态图标。卡住了没留话的，第二行说打开看看它需要什么。',
    file: 'src/components/common/Notification/WaitingList.vue',
    component: WaitingList,
    needs: UI_T,
    args: {
      loading: false,
      failed: false,
      errorReason: '',
      actionError: '',
      acting: null,
      clearing: null,
      replying: null,
    },
    states: [
      {
        name: '卡住、待回答、待审阅、退回、检查没过',
        note: '一件任务停在哪一格，行首就是那一格的状态图标；为什么点到你写在最前面。',
        props: {
          items: [
            waitingRow(0, 'blocked', 'owner', '没有预发环境的测试账号，登不进去验证', '预发环境验证登录'),
            waitingRow(1, 'blocked', 'owner', '', '导出成绩单'),
            waitingRow(2, 'awaiting_answer', 'asked', '作业 3 的截止时间定在周五还是周日？', '作业截止时间怎么定'),
            waitingRow(3, 'awaiting_review', 'reviewer', '登录表单加「记住我」', '作业 3 的参考答案'),
            waitingRow(4, 'bounced', 'owner', '按钮的颜色和设计稿不一致', '课程网站的导航栏'),
            waitingRow(5, 'checks_failed', 'owner', '', '部署课程网站'),
          ],
        },
        // 清单跨项目、每一行不带队友叫什么，所以那一句点的是默认名。
        expect: t('work.taskState.blockedFallback', { name: t('work.room.defaultAgentName') }),
      },
    ],
  },
]
