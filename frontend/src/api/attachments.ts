import type { AxiosProgressEvent } from 'axios'
import type { Attachment } from '@/types'

import { axiosRequest } from './axiosHttp'

export type UploadAttachmentRequestData = {
  type: string
  file: File
}

export type UploadAttachmentResponseData = {
  id: number
}

export type GetAttachmentDetailResponse = {
  attachment: Attachment
}

/** 单份附件的上限：一个文件最多多少字节。
 *
 *  后端报的是**执行上限的那个配置本身**（`settings.attachment_max_bytes`，
 *  `GET /attachments/limits`），所以这张卡上写的数与传上去被拒的那个数不会走散。 */
export type GetAttachmentLimitsResponse = {
  maxFileBytes: number
}

/** Attachments ride the primary (legacy) API origin; calls expose only the payload.
 *  The upload dials its own 60s timeout (above the 30s instance default) so a large
 *  file is not cut off, and forwards axios upload-progress events. */
export namespace AttachmentsApi {
  export const upload = (data: UploadAttachmentRequestData, onProgress?: (event: AxiosProgressEvent) => void) => {
    const formData = new FormData()
    formData.append('type', data.type)
    formData.append('file', data.file)

    return axiosRequest<UploadAttachmentResponseData>({
      url: '/attachments',
      method: 'POST',
      data: formData,
      timeout: 60000,
      onUploadProgress: onProgress,
    })
  }

  /** 单份附件的上限，上传之前先问一次 —— 与上传同一道门（登录即可）。 */
  export const limits = () => axiosRequest<GetAttachmentLimitsResponse>({ url: '/attachments/limits', method: 'GET' })

  export const detail = (id: number) =>
    axiosRequest<GetAttachmentDetailResponse>({ url: `/attachments/${id}`, method: 'GET' })
}
