// 题目：挂在空间（板）上的一道赛题，`/tasks/…` 那一族端点。与 `@/api/tasks`（房间
// 任务，地址 `/topics/{task}` ＋ `readSince`/`signal` 的清单）是两个东西 —— 名字只
// 为避开它。返回的都是 payload（新栈），不再是整只 `{code,message,data}` 信封。
//
// 从旧网络栈搬过来时地址、查询、正文与超时一字未改。题目的材料在 `@/api/taskFiles`。
import type {
  EncodedCursorPage,
  Page,
  Task,
  TaskMembership,
  TaskProgress,
  TaskStage,
  TaskSubmission,
  TeamSummary,
} from '@/types'
import type {
  AddTaskParticipantRequestData,
  PatchTaskParticipantRequestData,
  PatchTaskRequestData,
  PatchTaskSubmissionReviewRequestData,
  PostTaskRequestData,
  PostTaskSubmissionRequestData,
  PostTaskSubmissionReviewRequestData,
  TaskInheritanceData,
  TaskParticipationInfo,
} from '@/types/taskBoard'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** 搜到的一道题目：叫什么、在哪个空间。 */
export interface FoundTask {
  id: number
  name: string
  spaceId: number
  spaceName: string
  deadline: number | null
}

export namespace TaskBoardApi {
  /** Ctrl+K：我加入的空间里名字或简介含这些词的题目，照各空间列表给我看的那样。 */
  export const search = (q: string) =>
    axiosRequest<{ tasks: FoundTask[] }>({ url: '/tasks/search', method: 'GET', params: { q } }, true)

  export const create = (data: PostTaskRequestData) =>
    axiosRequest<{ task: Task }>({ url: '/tasks', method: 'POST', data }, true)

  export const update = (taskId: number, data: PatchTaskRequestData) =>
    axiosRequest<{ task: Task }>({ url: `/tasks/${taskId}`, method: 'PATCH', data }, true)

  export const del = (taskId: number) => axiosMutation({ url: `/tasks/${taskId}`, method: 'DELETE' }, true)

  export const detail = (
    taskId: number,
    params: {
      querySpace?: boolean
      queryJoinability?: boolean
      querySubmittability?: boolean
      queryTopics?: boolean
      queryJoined?: boolean
      queryJoinedApproved?: boolean
      queryJoinedDisapproved?: boolean
      queryJoinedNotApprovedOrDisapproved?: boolean
    } = {
      querySpace: true,
      queryJoinability: true,
      querySubmittability: true,
      queryTopics: true,
      queryJoined: true,
      queryJoinedApproved: true,
      queryJoinedDisapproved: true,
      queryJoinedNotApprovedOrDisapproved: true,
    }
  ) =>
    axiosRequest<{ task: Task; participation?: TaskParticipationInfo }>(
      { url: `/tasks/${taskId}`, method: 'GET', params },
      true
    )

  export const list = (params: {
    space?: number
    team?: number
    owner?: number
    pageSize?: number
    pageStart?: string
    sort_by: 'createdAt' | 'updatedAt' | 'deadline' | 'publishedAt' | 'reviewedAt'
    sort_order: 'asc' | 'desc'
    querySpace?: boolean
    queryJoinability?: boolean
    querySubmittability?: boolean
    queryJoined?: boolean
    queryTopics?: boolean
    /** 让列表带上每道题的提交表单（审核页要显示「提交要求」才需要）。 */
    querySubmissionSchema?: boolean
    keywords?: string
    approved?: 'APPROVED' | 'DISAPPROVED' | 'NONE'
    joined?: boolean
    topics?: number[]
    categoryId?: number
    /** `recruiting`：没结束、报名没截止、还有名额；`closed`：其余的。 */
    lifecycle?: 'ended' | 'recruiting' | 'closed' | 'notEnded'
    limitedView?: boolean
    /** 让服务端在**同一个响应**里多带一个 `distinctParticipants`：这一页题目上去重
     *  后的参与人数（一个人领三道题算一人）。逐题的 `participants.total` 只能求和当
     *  「领取次数」，去重的人数在客户端拼不出来，所以只能问服务端要。
     *
     *  默认不问：服务端为它要多跑一次本题目的报名名单查询，首页那种「数字要和列表
     *  一起上屏」的地方才值这一趟。 */
    queryDistinctParticipants?: boolean
  }) => {
    const finalParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        if (Array.isArray(value)) {
          value.forEach((v) => finalParams.append(key, v.toString()))
        } else {
          finalParams.set(key, value.toString())
        }
      }
    })
    return axiosRequest<{
      tasks: Task[]
      page: EncodedCursorPage
      /** 只在 `queryDistinctParticipants` 为真时才有：这一页 `tasks` 上去重后的
       *  参与人数。 */
      distinctParticipants?: number
    }>({ url: '/tasks', method: 'GET', params: finalParams }, true)
  }

  export const addParticipant = (taskId: number, member: number, data: AddTaskParticipantRequestData = {}) =>
    axiosRequest<{ task: Task }>(
      { url: `/tasks/${taskId}/participants`, method: 'POST', params: { member }, data },
      true
    )

  export const join = (taskId: number, data: AddTaskParticipantRequestData, teamId?: number) =>
    axiosRequest<{
      participant: TaskMembership
      /** 开始了才有：直接开始的题当场建出来；申请制的题申请时是 null，通过后再开始才建。 */
      project: { id: string; name: string; root_topic_id: string; team_id: number } | null
    }>(
      {
        url: `/tasks/${taskId}/participations/${teamId === undefined ? 'user' : 'team'}`,
        method: 'POST',
        data: teamId === undefined ? data : { ...data, teamId },
      },
      true
    )

  /**
   * 建这道题的项目会继承什么 (#944)：资源包、四层合成后的「给 AI 队友的指导」
   * （含来源层）、以及会被带上的资料。
   *
   * 只在「领取 / 从这道题建项目」那一步用得上，所以不塞进题目详情里 —— 详情每
   * 一次列表、每一次预览都要付它那一串查询。
   */
  export const inheritance = (taskId: number) =>
    axiosRequest<TaskInheritanceData>({ url: `/tasks/${taskId}/inheritance`, method: 'GET' }, true)

  // 获取可参与任务的队伍列表
  export const getTaskTeams = (taskId: number, params: { filter?: 'all' | 'eligible' } = { filter: 'all' }) =>
    axiosRequest<{ teams: TeamSummary[] }>({ url: `/tasks/${taskId}/teams`, method: 'GET', params }, true)

  export const removeParticipant = (taskId: number, participantId: number) =>
    axiosRequest<{ task: Task }>({ url: `/tasks/${taskId}/participants/${participantId}`, method: 'DELETE' }, true)

  export const removeParticipantByMemberId = (taskId: number, memberId: number) =>
    axiosRequest<{ task: Task }>(
      { url: `/tasks/${taskId}/participants`, method: 'DELETE', params: { member: memberId } },
      true
    )

  export const getParticipants = (
    taskId: number,
    params: { queryRealNameInfo?: boolean; queryTeamInfo?: boolean } = { queryRealNameInfo: true }
  ) =>
    axiosRequest<{ participants: TaskMembership[] }>(
      { url: `/tasks/${taskId}/participants`, method: 'GET', params },
      true
    )

  export const updateParticipant = (taskId: number, participantId: number, data: PatchTaskParticipantRequestData) =>
    axiosRequest<{ task: Task }>({ url: `/tasks/${taskId}/participants/${participantId}`, method: 'PATCH', data }, true)

  export const updateParticipantByMemberId = (
    taskId: number,
    memberId: number,
    data: PatchTaskParticipantRequestData
  ) =>
    axiosRequest<{ task: Task }>(
      { url: `/tasks/${taskId}/participants`, method: 'PATCH', params: { member: memberId }, data },
      true
    )

  /** 交一版：`stage` 是交哪个阶段（阶段的 id），不分阶段的题不带。 */
  export const createSubmission = (
    taskId: number,
    participantId: number,
    data: PostTaskSubmissionRequestData[],
    stage: string | null = null
  ) =>
    axiosRequest<{ submission: TaskSubmission }>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/submissions`,
        method: 'POST',
        data,
        params: stage ? { stage } : undefined,
      },
      true
    )

  /** 每个阶段上的进度：我的，和（批改的人才有）全体的分布。 */
  export const progress = (taskId: number) =>
    axiosRequest<TaskProgress>({ url: `/tasks/${taskId}/progress`, method: 'GET' }, true)

  /** 把题目的阶段存成 `stages` 会让哪些领取被判未通过、哪些从未通过回来，因为哪几个阶段（只算不写）。 */
  export const stageEffect = (taskId: number, stages: TaskStage[]) =>
    axiosRequest<{
      failing: number
      reopening: number
      failingNames: string[]
      reopeningNames: string[]
      failingStages: string[]
    }>({ url: `/tasks/${taskId}/stages/effect`, method: 'POST', data: { stages } }, true)

  export const updateSubmission = (
    taskId: number,
    participantId: number,
    version: number,
    data: PostTaskSubmissionRequestData[]
  ) =>
    axiosRequest<{ submission: TaskSubmission }>(
      { url: `/tasks/${taskId}/participants/${participantId}/submissions/${version}`, method: 'PATCH', data },
      true
    )

  export const listSubmissions = (
    taskId: number,
    participantId: number,
    params: {
      allVersions?: boolean
      pageSize?: number
      pageStart?: number
      sort_by: 'createdAt' | 'updatedAt'
      sort_order: 'asc' | 'desc'
      queryReview?: boolean
      /** 只要这个阶段（阶段的 id）的提交。 */
      stage?: string
    }
  ) =>
    axiosRequest<{ submissions: TaskSubmission[]; page: Page }>(
      { url: `/tasks/${taskId}/participants/${participantId}/submissions`, method: 'GET', params },
      true
    )

  export const postSubmissionReview = (
    taskId: number,
    participantId: number,
    submissionId: number,
    data: PostTaskSubmissionReviewRequestData
  ) =>
    axiosRequest<{ submission: TaskSubmission }>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
        method: 'POST',
        data,
      },
      true
    )

  export const patchSubmissionReview = (
    taskId: number,
    participantId: number,
    submissionId: number,
    data: PatchTaskSubmissionReviewRequestData
  ) =>
    axiosRequest<{ submission: TaskSubmission }>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
        method: 'PATCH',
        data,
      },
      true
    )

  export const deleteSubmissionReview = (taskId: number, participantId: number, submissionId: number) =>
    axiosRequest<{ submission: TaskSubmission }>(
      {
        url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
        method: 'DELETE',
      },
      true
    )

  export const resubmitTask = (taskId: number) =>
    axiosRequest<{ task: Task }>({ url: `/tasks/${taskId}/resubmit`, method: 'POST' }, true)
}
