import type { Attachment } from '@/types'

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
