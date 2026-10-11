/**
 * 预览站「题目板拆出来的那半张画面」第二批（`catalogScenesSpacesDetail.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着各自的 `.spec.ts` 和所在的 `XView.vue` 造：成员行照
 * `MembersView.vue` 自己导出的 `Row`。这里只出数据，不引运行时要装的东西：哪一份配哪个
 * 组件、每一格看什么，在 `catalogScenesSpacesDetail.ts`。
 */
import type { SpaceAnnouncement, SpaceMyPublishedTask, Task } from '@/types'
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
    participants: { total: 0, examples: [] },
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
