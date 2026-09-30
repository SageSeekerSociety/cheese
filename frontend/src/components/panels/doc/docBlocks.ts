// 服务端的节点树与屏幕上那些块之间的对位：两边都是从同一份文档按同一个顺序来的，所以
// 「第几段」是一个位置上的对号入座。
//
// 数长度这件事有一个坑，在这里收着，免得两处各写一份、各错一样：tiptap 会在块内容后面补
// 一个空段落（好让你点它下面打字），服务端的节点树里没有它 —— 而且这种收尾空段可能不止
// 一个。所以「渲染出来的块」要先按同一套规矩把尾巴削干净，才和服务端那一串长得一样。
//
// 为什么住在这个目录而不是 lib/：它读的是编辑器的 DOM，不是内容本身。lib/ 里那些
// (docMarkdown、docEditState) 是不碰 DOM 的，混进去会让「lib 里能跑单测」这条线糊掉。
import type { Block } from '../../../cx_types'

import { nextTick } from 'vue'

/** 一个不带任何内容的收尾段落：tiptap 补出来的那个。 */
function isFillerBlock(el: HTMLElement): boolean {
  return el.tagName === 'P' && el.textContent?.trim() === ''
}

/** 屏幕上对应着服务端节点的那些块 —— 削掉尾巴上的空段落。 */
export function contentBlocks(): HTMLElement[] {
  const els = Array.from(document.querySelectorAll('.doc-editor .ProseMirror > *')) as HTMLElement[]
  while (els.length && isFillerBlock(els[els.length - 1])) els.pop()
  return els
}

/**
 * 把节点树和屏幕上的块对上号。tiptap 装完 DOM 是异步落下的，所以重试几帧；一直对不上
 * 就返回 []，调用方据此降级（整篇闪一下、或者一条不锚定在段落上的评论）。
 */
export async function alignedDocBlocks(
  fetchDocNodes: () => Promise<Block[]>
): Promise<{ node: Block; el: HTMLElement }[]> {
  let nodes: Block[]
  try {
    nodes = await fetchDocNodes()
  } catch {
    return []
  }
  for (let attempt = 0; attempt < 20; attempt++) {
    await nextTick()
    const els = contentBlocks()
    if (els.length === nodes.length) {
      return nodes.map((node, i) => ({ node, el: els[i] }))
    }
    await new Promise((r) => window.setTimeout(r, 100))
  }
  return []
}
