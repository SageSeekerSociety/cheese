// 附件里的**字节**从哪里来：一条消息里的那张图（AttachmentImage.vue）、一个待发条上
// 的文档缩略图（AttachmentDocThumb.vue）都要。
//
// 为什么是一个注入口，而不是组件自己去取：这些字节的端点从 Authorization 头认人，
// `<img src>` 带不了头，直接挂 URL 拿到的是 401，所以要先 fetch、再转 object URL——
// 这两步都在 src/api.ts 里，而那个模块连着后端。组件一旦自己 import 它，组件和渲染它
// 的每一个组件就都没法离开后端单独挂起来（.claude/scripts/frontend_grade.py 判 C），
// 而这张图散在对话栏、工作面板和公共页里。所以组件只认这个接口，真的由外壳在 App.vue
// 注入（composables/useAttachmentSource.ts）；没人注入的树——组件目录、单独挂载的
// 测试——拿到 NO_ATTACHMENT_SOURCE：取不到字节，组件按「读不到」画，不崩。
//
// 这里只放类型、键和那个兜底，不 import api：import 了，整条链又被拖回「连着后端」。
import type { InjectionKey } from 'vue'

export interface AttachmentSource {
  /**
   * 一张图片附件的 object URL。拿到的人负责在换地址或卸载时 `URL.revokeObjectURL`。
   * 取不到就抛，调用方自己决定画什么。
   */
  imageUrl(topicId: string, path: string): Promise<string>
  /** 一份要转换才能预览的文档（.docx 这类）的 PDF 字节。 */
  documentPdf(topicId: string, path: string): Promise<ArrayBuffer>
  /** 一份附件的原始字节（能直接交给浏览器看的格式走这条）。 */
  fileBytes(topicId: string, path: string): Promise<ArrayBuffer>
}

/** 没人注入时的样子：没有字节可取。 */
export const NO_ATTACHMENT_SOURCE: AttachmentSource = {
  imageUrl: () => Promise.reject(new Error('no attachment source')),
  documentPdf: () => Promise.reject(new Error('no attachment source')),
  fileBytes: () => Promise.reject(new Error('no attachment source')),
}

export const ATTACHMENT_SOURCE: InjectionKey<AttachmentSource> = Symbol('attachmentSource')
