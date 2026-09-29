import type { AxiosProgressEvent } from 'axios'
import type {
  GetAttachmentDetailResponse,
  GetAttachmentLimitsResponse,
  UploadAttachmentRequestData,
  UploadAttachmentResponseData,
} from './types'

import ApiInstance from '../index'

export namespace AttachmentsApi {
  export const upload = (
    data: UploadAttachmentRequestData,
    onProgress?: (progressEvent: AxiosProgressEvent) => void
  ) => {
    const formData = new FormData()
    formData.append('type', data.type)
    formData.append('file', data.file)

    return ApiInstance.request<UploadAttachmentResponseData>({
      url: '/attachments',
      method: 'POST',
      data: formData,
      timeout: 60000,
      onUploadProgress: onProgress,
      // headers: {
      //   'Content-Type': 'multipart/form-data',
      // },
    })
  }

  /** 单份附件的上限，上传之前先问一次 —— 与上传同一道门（登录即可）。 */
  export const limits = () =>
    ApiInstance.request<GetAttachmentLimitsResponse>({
      url: '/attachments/limits',
      method: 'GET',
    })

  export const detail = (id: number) =>
    ApiInstance.request<GetAttachmentDetailResponse>({
      url: `/attachments/${id}`,
      method: 'GET',
    })
}
