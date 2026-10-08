// 外壳给附件链注入的真东西（接口见 lib/attachmentSource.ts）：字节还是从 src/api.ts
// 取，那三个函数本来就认得去哪儿拿。只在 App.vue 调一次。
import { provide } from 'vue'

import { attachmentImageUrl, previewDocumentPdf, previewFileBytes } from '@/api'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

export function provideAttachmentSource(): void {
  provide(ATTACHMENT_SOURCE, {
    imageUrl: attachmentImageUrl,
    documentPdf: previewDocumentPdf,
    fileBytes: previewFileBytes,
  })
}
