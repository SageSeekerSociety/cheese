/** A problem's attachment has no public URL: bytes go through the guarded download endpoint. */
export interface TaskAttachmentData {
  id: number
  name: string
  size: number
  contentType: string
  uploaderId?: number | null
  downloadCount: number
  createdAt: number
}

export type TaskAttachmentListResponseData = {
  attachments: TaskAttachmentData[]
  /** Download permission comes from the server, not from the attachment's presence. */
  canDownload: boolean
}
