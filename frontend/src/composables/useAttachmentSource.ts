// 外壳给附件链注入的真东西（接口见 lib/attachmentSource.ts）：字节还是从 src/api.ts
// 取，那三个函数本来就认得去哪儿拿。只在 App.vue 调一次。
import { provide } from 'vue'

import {
  attachmentImageUrl,
  attachmentMediaUrl,
  getPreview,
  previewDocumentPdf,
  previewFileBytes,
  roomFileRevisionBytes,
  roomFileRevisions,
} from '@/api'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { FILE_FACTS_SOURCE } from '@/lib/fileFactsSource'

export function provideAttachmentSource(): void {
  provide(ATTACHMENT_SOURCE, {
    imageUrl: attachmentImageUrl,
    mediaUrl: attachmentMediaUrl,
    documentPdf: previewDocumentPdf,
    fileBytes: previewFileBytes,
  })
  provide(FILE_FACTS_SOURCE, {
    versions: async (topicId, path) => (await roomFileRevisions(topicId, path)).data,
    versionBytes: roomFileRevisionBytes,
    livePreview: async (topicId) => {
      const info = await getPreview(topicId)
      if (!info) return null
      return { artifactId: info.artifact_id ?? null, running: !!info.url, tunnelUp: !!info.tunnel_up }
    },
  })
}
