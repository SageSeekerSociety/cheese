// src/api/tasks.ts

import type { EncodedCursorPage, Page, TaskMembership, TaskSubmission, TeamSummary } from '@/types'
import type { Task } from '@/types'
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
  TaskParticipationInfo,
  UploadTaskAttachmentResponseData,
} from './types'

import { AxiosProgressEvent } from 'axios'

import { NewApiInstance } from '../index'

import { refreshSession } from '@/lib/session'

export namespace TasksApi {
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

    return NewApiInstance.request<PreviewTaskFromPdfResponseData>({
      url: '/tasks/publish/from-pdf/preview',
      method: 'POST',
      data: formData,
      timeout: PDF_TIMEOUT_MS,
    })
  }

  /**
   * 确认并批量发布 PDF 解析生成的赛题草稿
   * @param data - 包含待发布草稿列表的请求数据
   * @returns 已创建的赛题列表和数量
   */
  export const confirmFromPdf = (data: ConfirmTaskFromPdfRequestData) =>
    NewApiInstance.request<ConfirmTaskFromPdfResponseData>({
      url: '/tasks/publish/from-pdf/confirm',
      method: 'POST',
      data,
      timeout: PDF_TIMEOUT_MS,
    })

  export const create = (data: PostTaskRequestData) =>
    NewApiInstance.request<{ task: Task }>({
      url: '/tasks',
      method: 'POST',
      data,
    })

  export const update = (taskId: number, data: PatchTaskRequestData) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}`,
      method: 'PATCH',
      data,
    })

  /** 一道题上的材料清单。看得见这道题的人都拿得到，能不能下载在 `canDownload` 里。 */
  export const listAttachments = (taskId: number) =>
    NewApiInstance.request<TaskAttachmentListResponseData>({
      url: `/tasks/${taskId}/attachments`,
      method: 'GET',
    })

  /** 传一个文件并挂到这道题上（出题人本人或板管理员）。 */
  export const uploadAttachment = (
    taskId: number,
    file: File,
    onProgress?: (progressEvent: AxiosProgressEvent) => void
  ) => {
    const formData = new FormData()
    formData.append('file', file)

    return NewApiInstance.request<UploadTaskAttachmentResponseData>({
      url: `/tasks/${taskId}/attachments`,
      method: 'POST',
      data: formData,
      timeout: 60000,
      onUploadProgress: onProgress,
    })
  }

  /** 把一份材料从这道题上摘下来。存储上的对象留着（见后端 `TaskAttachmentService.remove`）。 */
  export const removeAttachment = (taskId: number, attachmentId: number) =>
    NewApiInstance.request({
      url: `/tasks/${taskId}/attachments/${attachmentId}`,
      method: 'DELETE',
    })

  export const del = (taskId: number) =>
    NewApiInstance.request({
      url: `/tasks/${taskId}`,
      method: 'DELETE',
    })

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
    NewApiInstance.request<{ task: Task; participation?: TaskParticipationInfo }>({
      url: `/tasks/${taskId}`,
      method: 'GET',
      params,
    })

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
    return NewApiInstance.request<{
      tasks: Task[]
      page: EncodedCursorPage
      /** 只在 `queryDistinctParticipants` 为真时才有：这一页 `tasks` 上去重后的
       *  参与人数。 */
      distinctParticipants?: number
    }>({
      url: '/tasks',
      method: 'GET',
      params: finalParams,
    })
  }

  export const addParticipant = (
    taskId: number,
    member: number,
    data: AddTaskParticipantRequestData = { deadline: null }
  ) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/participants`,
      method: 'POST',
      params: { member },
      data,
    })

  export const join = (taskId: number, data: AddTaskParticipantRequestData, teamId?: number) =>
    NewApiInstance.request<{
      participant: TaskMembership
      project: { id: string; name: string; root_topic_id: string; team_id: number }
    }>({
      url: `/tasks/${taskId}/participations/${teamId === undefined ? 'user' : 'team'}`,
      method: 'POST',
      data: teamId === undefined ? data : { ...data, teamId },
    })

  // 获取可参与任务的队伍列表
  export const getTaskTeams = (
    taskId: number,
    params: {
      filter?: 'all' | 'eligible'
    } = { filter: 'all' }
  ) =>
    NewApiInstance.request<{ teams: TeamSummary[] }>({
      url: `/tasks/${taskId}/teams`,
      method: 'GET',
      params,
    })

  export const removeParticipant = (taskId: number, participantId: number) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/participants/${participantId}`,
      method: 'DELETE',
    })

  export const removeParticipantByMemberId = (taskId: number, memberId: number) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/participants`,
      method: 'DELETE',
      params: { member: memberId },
    })

  export const getParticipants = (
    taskId: number,
    params: { queryRealNameInfo?: boolean; queryTeamInfo?: boolean } = { queryRealNameInfo: true }
  ) =>
    NewApiInstance.request<{ participants: TaskMembership[] }>({
      url: `/tasks/${taskId}/participants`,
      method: 'GET',
      params,
    })

  export const updateParticipant = (taskId: number, participantId: number, data: PatchTaskParticipantRequestData) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/participants/${participantId}`,
      method: 'PATCH',
      data,
    })

  export const updateParticipantByMemberId = (
    taskId: number,
    memberId: number,
    data: PatchTaskParticipantRequestData
  ) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/participants`,
      method: 'PATCH',
      params: { member: memberId },
      data,
    })

  export const createSubmission = (taskId: number, participantId: number, data: PostTaskSubmissionRequestData[]) =>
    NewApiInstance.request<{ submission: TaskSubmission }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions`,
      method: 'POST',
      data,
    })

  export const updateSubmission = (
    taskId: number,
    participantId: number,
    version: number,
    data: PostTaskSubmissionRequestData[]
  ) =>
    NewApiInstance.request<{ submission: TaskSubmission }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions/${version}`,
      method: 'PATCH',
      data,
    })

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
    NewApiInstance.request<{ submissions: TaskSubmission[]; page: Page }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions`,
      method: 'GET',
      params,
    })

  export const postSubmissionReview = (
    taskId: number,
    participantId: number,
    submissionId: number,
    data: PostTaskSubmissionReviewRequestData
  ) =>
    NewApiInstance.request<{ submission: TaskSubmission }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
      method: 'POST',
      data,
    })

  export const patchSubmissionReview = (
    taskId: number,
    participantId: number,
    submissionId: number,
    data: PatchTaskSubmissionReviewRequestData
  ) =>
    NewApiInstance.request<{ submission: TaskSubmission }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
      method: 'PATCH',
      data,
    })

  export const deleteSubmissionReview = (taskId: number, participantId: number, submissionId: number) =>
    NewApiInstance.request<{ submission: TaskSubmission }>({
      url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/review`,
      method: 'DELETE',
    })

  export const resubmitTask = (taskId: number) =>
    NewApiInstance.request<{ task: Task }>({
      url: `/tasks/${taskId}/resubmit`,
      method: 'POST',
    })
}
