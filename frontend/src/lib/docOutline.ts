// 文档大纲：把正文里的标题按级别列出来，点了跳到那一段。
//
// 长文档（章程、周报、记忆）翻起来只能一路滚，看不出结构；这是「看一眼结构、再跳到
// 某一节」的那一半。它只读正文里**已经写好的**标题节点（见 lib/docSlashMenu.ts 里
// 编辑器提供的 h1–h3），不猜自然语言，也不新增任何文档节点 —— 大纲是正文的一个视图，
// 关掉它正文一个字都不会变。
//
// 住在 lib/ 而不是面板里：这是一段纯粹的解析（节点树进、标题数组出），面板里那层只
// 负责把结果画出来、把点击落成滚动。编辑器不认识大纲，大纲也不认识面板。
import type { Node as PMNode } from '@tiptap/pm/model'

/** 大纲里的一项：级别、标题文字、以及标题节点在文档里的位置。 */
export interface OutlineHeading {
  /** 1–3（编辑器只提供三级标题）。 */
  level: number
  /** 标题的文字，去掉了两端的空白。 */
  text: string
  /** 标题节点在文档里的位置（doc.descendants 给的那个，用来定位与滚动）。 */
  pos: number
}

/** 大纲收录到哪一级。编辑器只提供 h1–h3（lib/docSlashMenu.ts），更深的级别不列。 */
export const OUTLINE_MAX_LEVEL = 3

/**
 * 从一份文档里按出现顺序取出标题。
 *
 * 空标题不入列（没有文字可点，也没有文字可显示）；级别超出 1–3 的也不入列。
 */
export function extractOutline(doc: PMNode): OutlineHeading[] {
  const headings: OutlineHeading[] = []
  doc.descendants((node, pos) => {
    if (node.type.name !== 'heading') return true
    const level = typeof node.attrs.level === 'number' ? node.attrs.level : 0
    if (level < 1 || level > OUTLINE_MAX_LEVEL) return false
    const text = node.textContent.trim()
    if (text) headings.push({ level, text, pos })
    // 标题的子树是纯文字，没有必要再往下走。
    return false
  })
  return headings
}
