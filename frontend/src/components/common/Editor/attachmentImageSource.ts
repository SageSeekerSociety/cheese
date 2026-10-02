// 富文本里的图只存附件 id；换成地址、把新图传成附件，都要走接口。编辑器组件不碰接口层，
// 由应用在根上 provide 一份（plugins/index.ts）。没有 provide 的地方（组件预览、测试），
// 图只留出位置，插图按钮不出现。
import type { InjectionKey } from 'vue'

export interface UploadedImage {
  attachmentId: number
  width: number | null
  height: number | null
}

export interface AttachmentImageSource {
  url: (attachmentId: number) => Promise<string>
  upload: (file: File) => Promise<UploadedImage>
}

export const ATTACHMENT_IMAGE_SOURCE: InjectionKey<AttachmentImageSource> = Symbol('attachmentImageSource')
