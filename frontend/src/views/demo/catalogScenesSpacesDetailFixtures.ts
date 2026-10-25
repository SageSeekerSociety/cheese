/**
 * 预览站「题目板拆出来的那半张画面」第二批（`catalogScenesSpacesDetail.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着各自的 `.spec.ts` 和所在的 `XView.vue` 造：成员行照
 * `MembersView.vue` 自己导出的 `Row`，分析那几样照 `types/apiSpaces.ts` 的声明。这里
 * 只出数据，不引运行时要装的东西：哪一份配哪个组件、每一格看什么，在
 * `catalogScenesSpacesDetail.ts`。
 */
import type { SpaceAnnouncement, SpaceMyPublishedTask, Task } from '@/types'
import type {
  AnalyticsDistribution,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPublisherMetrics,
  SpaceAnalyticsTask,
} from '@/types/apiSpaces'
import type { Row } from '@/views/spaces/detail/MembersView.vue'
import type { TemplateDraft } from '@/views/spaces/detail/templateDraft'

import { blankTemplate } from '@/views/spaces/detail/templateDraft'

// ---- 成员（MembersView）----------------------------------------------------

/** 成员表里的一行：`MembersView.vue` 导出的 `Row`。`viaCode` 为空就是「没记录」。 */
export function memberRow(over: Partial<Row> = {}): Row {
  return {
    userId: 11,
    handle: 'ada',
    name: '阿达',
    avatarId: 101,
    role: 'MEMBER',
    viaCode: 'K7PM3QXW',
    ...over,
  }
}

// ---- 公告（AnnouncementsView）----------------------------------------------

/** 一条空间公告：`types/spaces.ts` 的 `SpaceAnnouncement`。`expiresAt` 非空就是有到期日。 */
export function announcement(over: Partial<SpaceAnnouncement> = {}): SpaceAnnouncement {
  return {
    id: 1,
    spaceId: 8,
    title: '学期安排',
    content: '<p>下周一开课。</p>',
    pinned: false,
    expiresAt: null,
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    author: { id: 1, username: 'cheese', nickname: '芝士', avatarId: null },
    ...over,
  }
}

// ---- 题目模板表单（TemplateFormView）--------------------------------------

/** 一份模板草稿：`templateDraft.ts` 的 `TemplateDraft`。底子是新建时的空白草稿。 */
export function templateDraft(over: Partial<TemplateDraft> = {}): TemplateDraft {
  return { ...blankTemplate(), name: '个人题模板', ...over }
}

// ---- 题目列表（TasksView）--------------------------------------------------

/** 列表里的一行：`types/tasks.ts` 的 `Task`。只填画面会读到的那几项。 */
export function task(over: Partial<Task> = {}): Task {
  return {
    id: 101,
    name: '最短路径',
    intro: '随便一句简介',
    submitterType: 'USER',
    creator: {
      id: 1,
      username: 'li-laoshi',
      nickname: '李老师',
      avatarId: 0,
      intro: '',
      question_count: 0,
      answer_count: 0,
    },
    deadline: null,
    participantLimit: 0,
    defaultDeadline: 7,
    resubmittable: false,
    editable: false,
    description: '',
    submissionSchema: [{ prompt: '提交文件', type: 'FILE' }],
    participants: { total: 0, approved: 0, examples: [] },
    submittable: false,
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    rank: 1,
    approved: 'APPROVED',
    requireRealName: false,
    ...over,
  }
}

/** 「我发布的」里的一行：`types/spaces.ts` 的 `SpaceMyPublishedTask`。 */
export function publishedTask(over: Partial<SpaceMyPublishedTask> = {}): SpaceMyPublishedTask {
  return {
    taskId: 101,
    taskName: '排课系统',
    category: { id: 7, name: '第 1 章' },
    approved: 'APPROVED',
    visibilityStatus: 'APPROVED_VISIBLE',
    isVisible: true,
    createdAt: 1757000000000,
    publishedAt: 1757000000000,
    endedAt: null,
    deadline: null,
    participantLimit: null,
    participantCount: 12,
    approvedParticipantCount: 10,
    pendingParticipantApprovalCount: 0,
    submittedParticipantCount: 8,
    pendingReviewCount: 0,
    successfulParticipantCount: 6,
    failedParticipantCount: 1,
    submissionConversionRate: 0.8,
    successRate: 0.75,
    latestSubmissionAt: null,
    ...over,
  }
}

// ---- 空间分析（OverviewView / ParticipantsView / PublishersView / TasksView）------

const WEEK = 7 * 24 * 60 * 60 * 1000

/** 一段趋势：桶按周排。报名一定多于提交、提交多于成功 —— 真数据就是这个方向。 */
function trend(counts: number[]) {
  return counts.map((count, index) => ({ bucket: 1757000000000 + index * WEEK, count }))
}

/** 一张分布：只给标和数。百分比后端可以不给，画的时候现算（`withDistributionPercent`）。 */
function distribution(items: Array<[string, number]>): AnalyticsDistribution {
  return { items: items.map(([label, count]) => ({ label, count })) }
}

/** 出题人下拉的候选项：页面取回来的那一份，组件自己会在前面补一项「全部」。 */
export const ANALYTICS_PUBLISHER_OPTIONS = [
  { title: '李老师', value: 3 },
  { title: '王助教', value: 4 },
]

/** 总览那一格的读数：`types/apiSpaces.ts` 的 `SpaceAnalyticsOverview`。 */
export function analyticsOverview(over: Partial<SpaceAnalyticsOverview> = {}): SpaceAnalyticsOverview {
  return {
    summary: { spaceId: 8, from: 1754400000000, to: 1757000000000 },
    entityMetrics: {
      taskCount: 12,
      publisherCount: 4,
      participantCount: 86,
      approvedParticipantCount: 74,
      submittedParticipantCount: 58,
      successfulParticipantCount: 47,
      participationConversionRate: 0.86,
      submissionConversionRate: 0.67,
      successRate: 0.55,
    },
    studentMetrics: { studentCount: 132, approvedStudentCount: 118, successfulStudentCount: 76 },
    taskDistributions: {
      byCategory: distribution([
        ['第 1 章', 7],
        ['第 2 章', 5],
      ]),
      byApprovalStatus: distribution([
        ['APPROVED', 10],
        ['NONE', 2],
      ]),
      byCompletionStatus: distribution([
        ['ALL_SUCCESS', 6],
        ['IN_PROGRESS', 4],
        ['NOT_STARTED', 2],
      ]),
    },
    trends: {
      tasksCreated: trend([2, 4, 6]),
      participantsJoined: trend([12, 30, 86]),
      submissionsCreated: trend([8, 21, 58]),
      successesAchieved: trend([5, 17, 47]),
    },
    ...over,
  }
}

/** 参与者那一格的读数与分布：`SpaceAnalyticsParticipants`。 */
export function analyticsParticipants(over: Partial<SpaceAnalyticsParticipants> = {}): SpaceAnalyticsParticipants {
  return {
    summary: { spaceId: 8, from: 1754400000000, to: 1757000000000 },
    entityMetrics: {
      participantCount: 86,
      approvedParticipantCount: 74,
      pendingParticipantCount: 8,
      disapprovedParticipantCount: 4,
      submittedParticipantCount: 58,
      successfulParticipantCount: 47,
    },
    studentMetrics: { studentCount: 132, studentsWithRealNameCount: 96 },
    distributions: {
      byApprovalStatus: distribution([
        ['APPROVED', 74],
        ['NONE', 8],
        ['DISAPPROVED', 4],
      ]),
      byCompletionStatus: distribution([
        ['SUCCESS', 47],
        ['PENDING_REVIEW', 6],
        ['NOT_SUBMITTED', 33],
      ]),
      byGrade: distribution([
        ['2024 级', 52],
        ['2025 级', 34],
      ]),
      byMajor: distribution([
        ['计算机科学与技术', 48],
        ['软件工程', 38],
      ]),
      byClassName: distribution([
        ['计科 2401', 27],
        ['计科 2402', 25],
        ['软工 2401', 38],
      ]),
      byRealNameStatus: distribution([
        ['WITH_REAL_NAME', 96],
        ['WITHOUT_REAL_NAME', 36],
      ]),
    },
    trends: {
      participantsJoined: trend([12, 30, 86]),
      submissionsCreated: trend([8, 21, 58]),
      successesAchieved: trend([5, 17, 47]),
    },
    ...over,
  }
}

/** 出题人明细表里的一行：`SpaceAnalyticsPublisherMetrics`。 */
export function analyticsPublisher(over: Partial<SpaceAnalyticsPublisherMetrics> = {}): SpaceAnalyticsPublisherMetrics {
  return {
    publisherId: 3,
    publisherName: '李老师',
    taskCount: 6,
    participantCount: 48,
    approvedParticipantCount: 41,
    submittedParticipantCount: 33,
    successfulParticipantCount: 27,
    avgParticipantsPerTask: 8,
    submissionConversionRate: 0.69,
    successRate: 0.56,
    lastTaskCreatedAt: 1757000000000,
    ...over,
  }
}

/** 明细表的一页：三个人的量级差开，上面那三张榜首卡才分得出各自是谁。 */
export function analyticsPublishers(): SpaceAnalyticsPublisherMetrics[] {
  return [
    analyticsPublisher(),
    analyticsPublisher({
      publisherId: 4,
      publisherName: '王助教',
      taskCount: 4,
      participantCount: 26,
      approvedParticipantCount: 22,
      submittedParticipantCount: 15,
      successfulParticipantCount: 11,
      avgParticipantsPerTask: 6.5,
      submissionConversionRate: 0.58,
      successRate: 0.42,
      lastTaskCreatedAt: 1756500000000,
    }),
    analyticsPublisher({
      publisherId: 5,
      publisherName: '陈老师',
      taskCount: 2,
      participantCount: 12,
      approvedParticipantCount: 11,
      submittedParticipantCount: 10,
      successfulParticipantCount: 9,
      avgParticipantsPerTask: 6,
      submissionConversionRate: 0.83,
      successRate: 0.75,
      lastTaskCreatedAt: 1755600000000,
    }),
  ]
}

/** 题目明细表里的一行：`SpaceAnalyticsTask`。`participantLimit` 为 null 就是没设上限。 */
export function analyticsTask(over: Partial<SpaceAnalyticsTask> = {}): SpaceAnalyticsTask {
  return {
    taskId: 101,
    taskName: '最短路径',
    publisher: { id: 3, name: '李老师' },
    category: { id: 7, name: '第 1 章' },
    approved: 'APPROVED',
    createdAt: 1757000000000,
    deadline: 1758000000000,
    participantCount: 18,
    participantLimit: 30,
    pendingParticipantApprovalCount: 2,
    approvedParticipantCount: 15,
    rejectedParticipantCount: 1,
    submittedParticipantCount: 12,
    pendingReviewCount: 3,
    resubmittableCount: 1,
    successfulParticipantCount: 9,
    failedParticipantCount: 2,
    submissionConversionRate: 0.67,
    successRate: 0.5,
    ...over,
  }
}
