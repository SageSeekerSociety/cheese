// 富文本里嵌的图只存附件 id（`attachmentImage` 节点的 `attachmentId`），画的时候才换成
// 地址；插图则是先传成附件、再把 id 写进正文。两件事都走附件接口；编辑器经由
// `ATTACHMENT_IMAGE_SOURCE` 拿到这里这一份（plugins/index.ts 在应用根上 provide）。
import type { AttachmentImageSource, UploadedImage } from '@/components/common/Editor/attachmentImageSource'
import type { ImageMeta } from '@/types'

import { getFullAttachmentUrl } from '@/utils/materials'

import { AttachmentsApi } from '@/network/api/attachments'

/** 一段正文里同一张图常出现多次，一页里也常有几段正文：同一个 id 只问一次。 */
const urls = new Map<number, Promise<string>>()

function attachmentImageUrl(id: number): Promise<string> {
  let url = urls.get(id)
  if (!url) {
    url = AttachmentsApi.detail(id).then(({ data }) => getFullAttachmentUrl(data.attachment.url))
    // 失败的不留着：下次再画还要能重试。
    url.catch(() => urls.delete(id))
    urls.set(id, url)
  }
  return url
}

async function uploadAttachmentImage(file: File): Promise<UploadedImage> {
  const {
    data: { id },
  } = await AttachmentsApi.upload({ type: 'image', file })
  const {
    data: { attachment },
  } = await AttachmentsApi.detail(id)
  urls.set(id, Promise.resolve(getFullAttachmentUrl(attachment.url)))
  const meta = attachment.meta as ImageMeta | null
  return { attachmentId: id, width: meta?.width ?? null, height: meta?.height ?? null }
}

export function useAttachmentImages(): AttachmentImageSource {
  return { url: attachmentImageUrl, upload: uploadAttachmentImage }
}
