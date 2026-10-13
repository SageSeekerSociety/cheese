import type { TaskAttachmentListResponseData } from '@/types/taskAttachments'

import { axiosRequest } from './axiosHttp'

export function listTaskAttachments(taskId: number): Promise<TaskAttachmentListResponseData> {
  return axiosRequest({ url: `/tasks/${taskId}/attachments`, method: 'GET' }, true)
}
