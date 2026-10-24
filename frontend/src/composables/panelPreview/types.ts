// 「预览」这一格对外的那一份 props、取数那一层的选项，和进出的两样载荷（要进房间的一张
// 图、画完的那张合成图）。取数那一半（`../usePanelPreview.ts`）和拆开的几个子模块都读它，
// 所以它单独放一处，而不是挂在组合根上。
import type { PreviewQuestion } from '@/lib/previewQuestion'
import type { ChatAttachment, ProjectMemberRow } from '../../cx_types'
import type { FramePick, usePreviewFrames } from '../usePreviewFrames'

export interface PanelPreviewProps {
  topicId: string | null
  projectId: string | null
  /** This tab is the one on screen. Loads happen on the rising edge. */
  active?: boolean
  /** Bumped by WorkPanel when a turn ends — silent re-fetch, never a spinner. */
  refreshTick?: number
  /**
   * 这一格看的是房间里指定的哪一份文件（工作面板自由区的一个页签）。不给就是
   * 固定的「预览」那一格：芝士最后摆出来的那一样，要跟着它走、要轮询。给了就只
   * 看这一份，房间的当前预览换成什么都和它无关。
   */
  path?: string | null
  /** 项目名册：文件评论里作者的名字和头像从它来（同 `PanelDoc` 的 `members`）。
   *  不给就只剩 handle 和色块——这是「没接线」的降级，不是一种画法。 */
  members?: ProjectMemberRow[]
  /** 这间房队友的 handle：评论里那个队友席位画的是谁。 */
  agentHandle?: string | null
  /** 这间房队友现在的名字（项目可以给它改名）：评论卡上写它、@ 候选里也用它。 */
  agentName?: string
}

export interface PanelPreviewOptions {
  /**
   * 授权表投进哪一个 iframe —— 展示组件渲染出来的那一个。POST 必须落在已经挂上的
   * frame 上：名字没对上，浏览器会开一个新标签页。
   */
  frameName: string
  /**
   * 元数据回来一次叫一次：这一格此刻指的是哪一件产物。房间拿它标「有新内容」。
   * 和以前一样，没换过的那一份也叫——判据在 WorkPanel 那一边。
   */
  onLoaded?: (artifactId: string | null) => void
  /** 帧里按了 ESC：怎么处理是画的那一半的事，这一层只往上递。 */
  onEscape?: () => void
  /** 帧里圈选了一处：画不画标注条、发不发引用是画的那一半的事，这一层只往上递。 */
  onPick?: (pick: FramePick) => void
  /** 第三方 View 里的表单点提交：这一层拼成一条普通提问再往上递。 */
  onAppMessage?: (question: PreviewQuestion) => void
  /** 编辑里「另存一份」另存出了一份新的：宿主要把它开成自由区的一个页签。 */
  onOpenFile?: (path: string) => void
}

/** 一张要进房间的图。上传真正要的只有这两样。 */
export interface ImageUpload {
  blob: Blob
  filename: string
}

/** 图上画完的那张合成图：展示组件把它做出来，取数这一层把它送进房间。 */
export interface AnnotateDraft extends ImageUpload {
  naturalWidth: number
  naturalHeight: number
  count: number
  note: string
}

/** 传一张图、换回一条能挂到消息上的附件。组件按 prop 拿它，自己不碰 fetch。
 *  收的只是「一张图」，因为要传的不止合成图一种：指出页面上的一点时，配图是那一页
 *  当时的样子，没有画过任何东西。 */
export type UploadAnnotation = (topicId: string, image: ImageUpload) => Promise<ChatAttachment>

/** 跑帧的那一包（`../usePreviewFrames.ts` 的返回）。帧的托管归它，这一层只照着它取状态、
 *  投授权表、导航——所以递给几个子模块的是它的形状，不是它的实现。 */
export type PreviewFrames = ReturnType<typeof usePreviewFrames>
