// 题目：挂在空间（板）上的一道赛题，`/tasks/…` 那一族端点。与 `@/api/tasks`（房间
// 任务，地址 `/topics/{task}` ＋ `readSince`/`signal` 的清单）是两个东西 —— 名字只
// 为避开它。返回的都是 payload（新栈），不再是整只 `{code,message,data}` 信封。
//
// 老模块 `@/network/api/tasks` 的 `TasksApi` 逐方法搬过来，地址、查询、正文与超时
// 一字不改：PDF 那两个用 `VITE_PDF_UPLOAD_TIMEOUT_MS`（缺省 600000），附件上传
// 60000 且带上传进度。
import type { EncodedCursorPage, Page, Task, TaskMembership, TaskSubmission, TeamSummary } from '@/types'
import type {
  AddTaskParticipantRequestData,
  ConfirmTaskFromPdfRequestData,
  ConfirmTaskFromPdfResponseData,
  CreateTaskFromPdfRequestData,
  PatchTaskParticipantRequestData,
  PatchTaskRequestData,
  PatchTaskSubmissionReviewRequestData,
  PostTaskRequestData,
  PostTaskSubmissionRequestData,
  PostTaskSubmissionReviewRequestData,
  PreviewTaskFromPdfResponseData,
  TaskAttachmentListResponseData,
  TaskInheritanceData,
  TaskParticipationInfo,
  UploadTaskAttachmentResponseData,
} from '@/types/taskBoard'

import { AxiosProgressEvent } from 'axios'

import { axiosMutation, axiosRequest } from './axiosHttp'

export namespace TaskBoardApi {
  /** PDF 上传/解析请求的超时时间（毫秒），可通过 VITE_PDF_UPLOAD_TIMEOUT_MS 环境变量配置 */
  const PDF_TIMEOUT_MS = Number(import.meta.env.VITE_PDF_UPLOAD_TIMEOUT_MS) || 600000

  /**
   * 上传 PDF 并解析生成赛题草稿预览
   * @param data - 包含空间ID、PDF文件、模板参数等的请求数据
   * @returns 解析出的赛题草稿列表、使用的模板信息及 token 消耗
   */
  export const previewFromPdf = (data: CreateTaskFromPdfRequestData) => {
    const formData = new FormData()
    formData.append('spaceId', data.spaceId.toString())
    formData.append('file', data.file)
    formData.append('templateIndex', (data.templateIndex ?? -1).toString())
    formData.append('maxTasks', (data.maxTasks ?? 5).toString())

    if (data.categoryId !== undefined && data.categoryId !== null) {
      formData.append('categoryId', data.categoryId.toString())
    }

    if (data.submitterType) {
      formData.append('submitterType', data.submitterType)
    }

    return axiosRequest<PreviewTaskFromPdfResponseData>(
      {
        url: '/tasks/publish/from-pdf/preview',
        method: 'POST',
        data: formData,
        timeout: PDF_TIMEOUT_MS,
      },
      true
    )
  }

  /**
   * 确认并批量发布 PDF 解析生成的赛题草稿
   * @param data - 包含待发布草稿列表的请求数据
   * @returns 已创建的赛题列表和数量
   */
  export const confirmFromPdf = (data: ConfirmTaskFromPdfRequestData) =>
    axiosRequest<ConfirmTaskFromPdfResponseData>(
      {
        url: '/tasks/publish/from-pdf/confirm',
        method: 'POST',
        data,
        timeout: PDF_TIMEOUT_MS,
      },
      true
    )

  export const create = (data: PostTaskRequestData) =>
    axiosRequest<{ task: Task }>({ url: '/tasks', method: 'POST', data }, true)

  export const update = (taskId: number, data: PatchTaskRequestData) =>
    axiosRequest<{ task: Task }>({ url: `/tasks/${taskId}`, method: 'PATCH', data }, true)

  /** 一道题上的材料清单。看得见这道题的人都拿得到，能不能下载在 `canDownload` 里。 */
  export const listAttachments = (taskId: number) =>
    axiosRequest<TaskAttachmentListResponseData>({ url: `/tasks/${taskId}/attachments`, method: 'GET' }, true)

  /** 传一个文件并挂到这道题上（出题人本人或板管理员）。 */
  export const uploadAttachment = (
    taskId: number,
    file: File,
    onProgress?: (progressEvent: AxiosProgressEvent) => void
  ) => {
    const formData = new FormData()
    formData.append('file', file)

    return axiosRequest<UploadTaskAttachmentResponseData>(
      {
        url: `/tasks/${taskId}/attachments`,
        method: 'POST',
        data: formData,
        timeout: 60000,
        onUploadProgress: onProgress,
      },
      true
    )
  }

  /** 把一份材料从这道题上摘下来。存储上的对象留着（见后端 `TaskAttachmentService.remove`）。 */
  export const removeAttachment = (taskId: number, attachmentId: number) =>
    axiosMutation({ url: `/tasks/${taskId}/attachments/${attachmentId}`, method: 'DELETE' }, true)

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
    lifecycle?: 'ended' | 'recruiting' | 'notEnded'
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

  export const addParticipant = (
    taskId: number,
    member: number,
    data: AddTaskParticipantRequestData = { deadline: null }
  ) =>
    axiosRequest<{ task: Task }>(
      { url: `/tasks/${taskId}/participants`, method: 'POST', params: { member }, data },
      true
    )

  export const join = (taskId: number, data: AddTaskParticipantRequestData, teamId?: number) =>
    axiosRequest<{
      participant: TaskMembership
      project: { id: string; name: string; root_topic_id: string; team_id: number }
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

  export const createSubmission = (taskId: number, participantId: number, data: PostTaskSubmissionRequestData[]) =>
    axiosRequest<{ submission: TaskSubmission }>(
      { url: `/tasks/${taskId}/participants/${participantId}/submissions`, method: 'POST', data },
      true
    )

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
