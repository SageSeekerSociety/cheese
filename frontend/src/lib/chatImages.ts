// 对话里的图能做的几件事，由拿着这段对话的那一层（ChatPanel）交给看图浮层。
//
// 浮层挂在一条消息的那张图里（AttachmentImage → ImageLightbox），它自己不知道这段
// 对话里还有哪些图、右侧面板在哪、输入框在哪——那三样都在 ChatPanel。一层层往上
// emit 要穿过 RoomMessage、ChatTimeline 好几层，而且浮层还会切到别的消息的图上；所以
// 走注入。没人注入的地方（反馈附件、公共页）浮层只看这一张，不出这几颗按钮。
import type { InjectionKey } from 'vue'
import type { Block } from '@/cx_types'

import { isImageBlock } from './blockDisplay'

/** 交给右侧面板时先选好的工具：框选批注、写字、打码。面板里那套涂画工具的名字。 */
export type ImageHandoffTool = 'select' | 'text' | 'redact'

/** 这里只用得上块的三样东西：从 `Block` 里挑，字段改了名这边会报错。 */
type ImageBlock = Pick<Block, 'kind' | 'mime_type' | 'content'>

/** 一行消息里的块：同一轮连着说的几块折在 `run` 里，第一块是 `block`。 */
export interface ImageRow {
  block: ImageBlock
  run: ImageBlock[]
}

/** 这段对话里的图，按出现的先后，同一张只算一次。
 *
 *  `run` 里折着的块也要算：同一个人连着发三张图，后面两张可能不在第一块里。 */
export function imagePaths(rows: readonly ImageRow[]): string[] {
  const out: string[] = []
  for (const row of rows) {
    for (const block of row.run.length ? row.run : [row.block]) {
      // 「是不是一张图」只问 `isImageBlock`：定义哪天放宽了，两边不会各走各的。
      if (isImageBlock(block) && !out.includes(block.content)) out.push(block.content)
    }
  }
  return out
}

export interface ChatImageActions {
  /** 这段对话里的图（工作区路径），按出现的先后：浮层左右切换就在这些里走。 */
  gallery: () => readonly string[]
  /** 在右侧面板打开这张图，并选好要用的工具。 */
  openInPanel: (path: string, tool: ImageHandoffTool) => void
  /** 浮层里编辑出来的新图：放进输入框，随下一条消息发出，原图不动。
   *  输入框此刻收不收文件（归档的频道、已关闭的任务里它不在）由 `canAttach` 说；
   *  收不了时这颗按钮就别出现，不然点了像交付成功，其实排进了一个看不见的队。 */
  attach: (file: File) => void
  canAttach: () => boolean
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
  if (!handed) return null
  if (Date.now() - handed.at > HANDOFF_MS) {
    // 过期就丢掉：留着它不会再见效，还会一直挂在这个模块变量上。
    pending = null
    return null
  }
  // 按 JSON 里那个完整的字符串比，不按子串：路径 `a/b.png` 不该认领 `uploads/x/a/b.png`。
  if (!identity.includes(JSON.stringify(handed.path))) return null
  pending = null
  return handed.tool
}
