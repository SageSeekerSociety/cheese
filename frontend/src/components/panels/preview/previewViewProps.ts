// PanelPreviewView 的 props 契约：这一格要什么，全写在这里。摆在视图外面，取数那一层和这一格看的是同一份说明，视图文件也留在一千行以内。
import type { DocumentRevisionsBundle } from '../../../composables/useDocumentRevisions'
import type { UploadAnnotation } from '../../../composables/usePanelPreview'
import type { PreviewFrame, PreviewNavigation } from '../../../composables/usePreviewFrames'
import type { RoomFileEditorBundle } from '../../../composables/useRoomFileEditor'
import type { RoomFileHistoryBundle } from '../../../composables/useRoomFileHistory'
import type { FileContent } from '../../../cx_types'
import type { DocumentIdentity, DocumentSnapshot } from '../../../lib/documentIdentity'
import type { FileKind } from '../../../lib/fileKind'
import type { SubmitPreviewQuestion } from '../../../lib/previewQuestion'
import type { SlideSource } from './slidesContext'

export interface PreviewViewProps {
  topicId: string | null
  submitQuestion?: SubmitPreviewQuestion
  /** 标注图的上传：取数那一层给的能力。这一格只调它，自己不碰 fetch。 */
  uploadAnnotation?: UploadAnnotation
  /** 这一格是不是正显示着的那一页：收起来的那几页不接全局键（见 DesignImage）。 */
  active?: boolean
  projectId: string | null
  /**
   * 这一格看的是房间里指定的哪一份文件（工作面板自由区的一个页签）。不给就是
   * 固定的「预览」那一格：芝士最后摆出来的那一样，要跟着它走、要轮询。给了就只
   * 看这一份，房间的当前预览换成什么都和它无关。
   */
  path?: string | null
  /** 授权表要落进的那个 iframe 的名字（取数那一层按它 POST）。 */
  frameName: string
  frames?: PreviewFrame[]
  displayedFrame?: PreviewFrame | null
  navigation?: PreviewNavigation
  navigationError?: string
  loading: boolean
  refreshing: boolean
  previewFile: FileContent | null
  previewMime: string
  previewNamed: boolean
  previewUrl: string | null
  previewAppNote: string
  previewTunnelUp: boolean
  previewNamedPath: string
  /** 刚跟着重启后的应用自动重载过：一句话解释那一闪，免得像是面板自己坏了。 */
  autoReloaded?: boolean
  previewError: string | null
  previewReadError: string | null
  /** 这一份是哪种文件：下面三样查看器和「是不是图片」都由它分派。 */
  documentSuffix: string
  documentType: FileKind | null
  documentName: string
  isImageArtifact: boolean
  downloadError: string
  docBytes: ArrayBuffer | null
  docIdentity?: DocumentIdentity | null
  docSnapshot?: DocumentSnapshot | null
  /** Identity verified against the actual conversion response, not current metadata alone. */
  slideContext?: SlideSource
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
  /** 读者挑的读法：网页那一档。默认还是转成 PDF 那一档。 */
  docPage: boolean
  /** 这一份有没有网页可换。只有 OfficeCLI 认得的那三种有，没有就不摆这个开关。 */
  canPage: boolean
  /** 网页那一页本身；还没取到就是 null。 */
  docPageHtml: string | null
  /** 这一份 .docx 的修订：清单、只读、处理动作都在里面（`useDocumentRevisions.ts`）。 */
  revs: DocumentRevisionsBundle
  /** 在线编辑那一份会话：编辑器实例、盯版本、另存都在里面（`useRoomFileEditor.ts`）。 */
  editor: RoomFileEditorBundle
  /** 同一栏历史：编辑器旁边那一栏和下面文档条那一栏读的是同一份（`useRoomFileHistory.ts`）。 */
  fileHistory: RoomFileHistoryBundle
  /** 现在开着哪一份的编辑会话；null 就是没开。开不开由外面记，因为关掉之后还要重取那一页。 */
  editing: string | null
  /** 文档条下面那一栏历史开着没有。 */
  showHistory: boolean
}
