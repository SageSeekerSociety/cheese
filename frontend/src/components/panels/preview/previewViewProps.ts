// PanelPreviewView 的 props 契约：这一格要什么，全写在这里。摆在视图外面，取数那一层和这一格看的是同一份说明，视图文件也留在一千行以内。
import type { AppWakeStep } from '../../../composables/panelPreview/appWake'
import type { PreviewCommentsBundle } from '../../../composables/panelPreview/comments'
import type { PanelPreviewLiveEdit } from '../../../composables/panelPreview/liveEdit'
import type { PreviewVersionsBundle } from '../../../composables/panelPreview/versions'
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
  /** 私下问个人芝士：那一句只进读者和芝士那一条对话，不进这间房。
   *
   *  和 `submitQuestion` 同一条道理，能力由页面递下来——面板自己不碰那个全局 store，
   *  它只发这一次意图。不给就是这一处没有这一项（没登录、或房东没有个人芝士）。 */
  askPersonal?: (message: string) => void
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
  /** 应用的端口有应答：连通链「应用」那一段。 */
  previewAppUp?: boolean
  /** 应用睡着时顶上的静态快照，和它是多久以前留的。 */
  appSnapshot?: { html: string; age: string } | null
  /** 唤醒的分步进度（没在唤醒是 null），和没成时那一句。 */
  wakeSteps?: AppWakeStep[] | null
  wakeError?: string
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
  docBytes: ArrayBuffer | null
  docIdentity?: DocumentIdentity | null
  docSnapshot?: DocumentSnapshot | null
  /** Identity verified against the actual conversion response, not current metadata alone. */
  slideContext?: SlideSource
  /** 演示稿每一页的演讲者备注，下标即页码。空数组是「这一份没有备注可显示」。 */
  slideNotes?: readonly string[]
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
  /** 屏幕上这一份读的是不是网页那一档：读者挑了网页，而且这一份给得出网页。两个条件
   *  都要，因为挑法是粘的（换文件不翻回去），而视图那几支问的是屏幕上有没有东西。默认
   *  那一档仍然是转成 PDF。 */
  docPage?: boolean
  /** 这一份有没有网页可换。只有 OfficeCLI 认得的那三种有，没有就不摆这个开关。 */
  canPage?: boolean
  /** 网页那一页本身；还没取到就是 null。 */
  docPageHtml?: string | null
  /** 这一份文件上的评论：线程、写一条新的、回复和解决
   *  （`composables/panelPreview/comments.ts`）。 */
  comments?: PreviewCommentsBundle
  /** 在预览格里和几个人一起改一份 markdown 的会话
   *  （`composables/panelPreview/liveEdit.ts`）。 */
  liveEdit?: PanelPreviewLiveEdit
  /** 这一份 .docx 的修订：清单、只读、处理动作都在里面（`useDocumentRevisions.ts`）。 */
  revs: DocumentRevisionsBundle
  /** 在线编辑那一份会话：编辑器实例、盯版本、另存都在里面（`useRoomFileEditor.ts`）。 */
  editor: RoomFileEditorBundle
  /** 同一栏历史：编辑器旁边那一栏和下面文档条那一栏读的是同一份（`useRoomFileHistory.ts`）。 */
  fileHistory: RoomFileHistoryBundle
  /** 文档条上的版本：第几版、本版改了几处、两版对比、看旧版（`composables/panelPreview/versions.ts`）。
   *  不给就不摆这一套（组件预览站、单测里那些只看正文的场景）。 */
  versions?: PreviewVersionsBundle
  /** 现在开着哪一份的编辑会话；null 就是没开。开不开由外面记，因为关掉之后还要重取那一页。 */
  editing: string | null
  /** 文档条下面那一栏历史开着没有。 */
  showHistory: boolean
  /** 这间房的队友叫什么：预览连不上时那句话请它去查（项目可以给它改名）。 */
  agentName?: string
}
