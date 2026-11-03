// 对话里一张文件卡片要的几样「这份文件现在怎样了」：存过几版、每版多大什么时候存的、
// 某一版的字节，以及（应用卡片）房间当前那个预览有没有在跑。
//
// 和 AttachmentSource 同一个道理（见 lib/attachmentSource.ts）：这几样都要连后端，卡片
// 自己 import 了 api，渲染它的每一层就都得跟着装后端。所以只认这个接口，外壳在
// App.vue 注入真的（composables/useAttachmentSource.ts）；没人注入时什么都取不到，
// 卡片只画它从消息本身知道的那些。
import type { InjectionKey } from 'vue'

/** 房间文件存下的一版。 */
export interface FileVersion {
  id: string
  /** 第几版，从 1 数起。 */
  seq: number
  size: number
  created_at: string
}

/** 房间当前那个预览。卡片只关心它是不是这一条、在不在跑。 */
export interface LivePreview {
  /** 摆出这个预览的那条消息。 */
  artifactId: string | null
  /** 应用应答了：能打开看。 */
  running: boolean
  /** 那台机器把预览通道拨出来了（应用没在跑时用它分「机器离线」和「应用没起」）。 */
  tunnelUp: boolean
}

export interface FileFactsSource {
  /** 这份文件存过的每一版，新的在前。 */
  versions(topicId: string, path: string): Promise<FileVersion[]>
  /** 某一版的字节。 */
  versionBytes(topicId: string, versionId: string): Promise<ArrayBuffer>
  /** 房间当前的预览；没有就是 null。 */
  livePreview(topicId: string): Promise<LivePreview | null>
}

export const NO_FILE_FACTS: FileFactsSource = {
  versions: () => Promise.reject(new Error('no file facts source')),
  versionBytes: () => Promise.reject(new Error('no file facts source')),
  livePreview: () => Promise.reject(new Error('no file facts source')),
}

export const FILE_FACTS_SOURCE: InjectionKey<FileFactsSource> = Symbol('fileFactsSource')
