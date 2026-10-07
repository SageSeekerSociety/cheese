/**
 * 频道主线上的任务卡（`TaskCard`、`TaskCreatedPost`）在预览站里吃的数据。
 *
 * 一张卡是一条 `TaskLine`（`lib/channelTasks.ts`）：卡面上那一句状态由 `taskLine()` 从
 * 后端的 `presentation` 算出来，这里照它的口径写 —— 文案取同一组 i18n 键
 * （`work.room.taskCard.*`、`work.board.phrase.*`、`work.board.mine.*`），不另造说法。
 * 人取自房间剧本（`scenes/turn.json`：王长鑫、李甘、芝士），和预览站里别的房间组件是
 * 同一拨人。
 *
 * 时间写成「几分钟前」而不是一个固定日期：卡上画的是相对时间（`relTime`），写死的
 * 日期过几天就从「5 分钟前」变成一个日期，那一格讲的就不是原来那件事了。
 */
import type { TaskLine } from '@/lib/channelTasks'

import { AGENT_NAME } from './catalogFixtures'

import { t } from '@/i18n'

function minutesAgo(n: number): string {
  return new Date(Date.now() - n * 60_000).toISOString()
}

const BASE: TaskLine = {
  id: 'task-syllabus',
  title: '整理第三周的课程资料',
  owner: 'wang',
  creator: 'wang',
  status: t('work.board.phrase.running'),
  tone: 'running',
  accepted: null,
  at: minutesAgo(5),
}

/** 芝士在跑：蓝点、「运行中」。 */
export const TASK_RUNNING: TaskLine = BASE

/** 在等别人审：写出等的是谁。 */
export const TASK_WAITING: TaskLine = {
  ...BASE,
  id: 'task-grading',
  title: '按评分标准批改第二次作业',
  owner: 'li',
  creator: 'li',
  status: t('work.room.taskCard.waitingReview', { name: '李甘' }),
  tone: 'waiting',
  at: minutesAgo(42),
}

/** 等的就是看的这个人：卡加一道强调边，状态说成「待你审阅」。 */
export const TASK_MINE: TaskLine = {
  ...BASE,
  id: 'task-welcome',
  title: '给新同学写一份入门说明',
  status: t('work.board.mine.awaiting_review'),
  tone: 'mine',
  accepted: t('work.room.taskCard.acceptedTimes', { n: 2 }),
  at: minutesAgo(90),
}

/** 检查没过：红点。 */
export const TASK_STUCK: TaskLine = {
  ...BASE,
  id: 'task-site',
  title: '把课程网站的目录页改成按周排',
  status: t('work.board.phrase.checks_failed'),
  tone: 'stuck',
  at: minutesAgo(12),
}

/** 采纳合并，做完了：一个勾。 */
export const TASK_DONE: TaskLine = {
  ...BASE,
  id: 'task-faq',
  title: '汇总上周的答疑记录',
  status: t('work.room.taskCard.acceptedPr', { pr: 128 }),
  tone: 'done',
  at: minutesAgo(60 * 26),
}

/** 没做就关了：一道横线。 */
export const TASK_CLOSED: TaskLine = {
  ...BASE,
  id: 'task-old',
  title: '试一下另一种排课方式',
  owner: null,
  status: t('work.board.phrase.closed'),
  tone: 'closed',
  at: minutesAgo(60 * 24 * 3),
}

/** 芝士自己新建的那一件（AI 队友署名，头像是芝士的那一个）。 */
export const TASK_BY_AGENT: TaskLine = {
  ...BASE,
  id: 'task-split',
  title: '把期末复习拆成三份讲义',
  owner: 'cheese',
  creator: 'cheese',
  status: t('work.board.phrase.discussing'),
  tone: 'discussing',
  at: minutesAgo(2),
}

/** `TaskCreatedPost` 那几样：谁新建的、叫什么、几点。 */
export function taskPostProps(task: TaskLine, over: Record<string, unknown> = {}) {
  const names: Record<string, string> = { wang: '王长鑫', li: '李甘', cheese: AGENT_NAME }
  return {
    task,
    creator: task.creator,
    creatorName: task.creator ? names[task.creator] ?? task.creator : '',
    ownerName: task.owner ? names[task.owner] ?? task.owner : null,
    avatar: null,
    time: '14:05',
    ...over,
  }
}
