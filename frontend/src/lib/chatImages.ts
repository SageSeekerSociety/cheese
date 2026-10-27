// 对话里的图能做的几件事，由拿着这段对话的那一层（ChatPanel）交给看图浮层。
//
// 浮层挂在一条消息的那张图里（AttachmentImage → ImageLightbox），它自己不知道这段
// 对话里还有哪些图、右侧面板在哪、输入框在哪——那三样都在 ChatPanel。一层层往上
// emit 要穿过 RoomMessage、ChatTimeline 好几层，而且浮层还会切到别的消息的图上；所以
// 走注入。没人注入的地方（反馈附件、公共页）浮层只看这一张，不出这几颗按钮。
import type { InjectionKey } from 'vue'

/** 交给右侧面板时先选好的工具：框选批注、写字、打码。面板里那套涂画工具的名字。 */
export type ImageHandoffTool = 'select' | 'text' | 'redact'

export interface ChatImageActions {
  /** 这段对话里的图（工作区路径），按出现的先后：浮层左右切换就在这些里走。 */
  gallery: () => readonly string[]
  /** 在右侧面板打开这张图，并选好要用的工具。 */
  openInPanel: (path: string, tool: ImageHandoffTool) => void
  /** 浮层里编辑出来的新图：放进输入框，随下一条消息发出，原图不动。 */
  attach: (file: File) => void
}

export const CHAT_IMAGE_ACTIONS: InjectionKey<ChatImageActions> = Symbol('chatImageActions')

/** 交接只在这么久里算数：面板没在这段时间里打开这张图，就当没交过。 */
const HANDOFF_MS = 15_000

let pending: { path: string; tool: ImageHandoffTool; at: number } | null = null

/** 记下「下一次右侧打开这张图时用哪个工具」。打开那一下走的是 open-file 那条老路，
 *  它只认路径，所以工具不跟着它走，在这里等面板来取。 */
export function handOffImageTool(path: string, tool: ImageHandoffTool): void {
  pending = { path, tool, at: Date.now() }
}

/** 面板里的图认出自己（标识里带着路径）时取走交接的工具；取一次就没了。 */
export function takeImageTool(identity: string): ImageHandoffTool | null {
  const handed = pending
  if (!handed || Date.now() - handed.at > HANDOFF_MS || !identity.includes(handed.path)) return null
  pending = null
  return handed.tool
}
