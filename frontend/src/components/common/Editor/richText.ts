// 题目详情、知识库、空间公告与模板、团队简介这几处富文本，和实况文档用的是同一套扩展：
// 下面先摆 `docExtensions()`，再补上老内容里会出现、实况文档用不到的几样。以后给文档写的
// 扩展（选中浮条、原位建议修改……）这几处自动就有，不必再写一份。
//
// 补的这几样不是新功能，是为了老内容读得回来。这几处的内容原先由另一台编辑器写出，存的是
// 它的 JSON（公告存 HTML）：文字颜色、字体、字号、高亮色、上下标、段落对齐、附件图。编辑器
// 读到 schema 里没有的节点或标记会把内容丢掉，没有的属性会悄悄去掉，所以这些都要认得。
// 工具栏上不给它们按钮，但带着它们的老内容打开、再保存，一样不少。
import type { AnyExtension, JSONContent, Node } from '@tiptap/core'

import Highlight from '@tiptap/extension-highlight'
import Subscript from '@tiptap/extension-subscript'
import Superscript from '@tiptap/extension-superscript'
import TextAlign from '@tiptap/extension-text-align'
import { Color, FontFamily, FontSize, TextStyle } from '@tiptap/extension-text-style'

import { AttachmentImage } from './attachmentImage'

import { docExtensions, parseMarkdown } from '@/lib/docSchema'

// 老编辑器存字号存的是不带单位的数（`"16"`），自己画的时候补 px。原样存回去，只在画的时候补。
const BARE_NUMBER = /^\d+(\.\d+)?$/
const LegacyFontSize = FontSize.extend({
  addGlobalAttributes() {
    return [
      {
        types: this.options.types,
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) => {
              const size = attributes.fontSize as string | number | null
              if (!size) return {}
              return { style: `font-size: ${BARE_NUMBER.test(String(size)) ? `${size}px` : size}` }
            },
          },
        },
      },
    ]
  },
})

// 老内容的高亮底色存在数据里（#FFEB3B 这类浅色），两套主题下都不变；底色不变，字色也得
// 跟着不变，否则深色主题下浅色字压在黄底上读不出来。这个深色取浅色主题的 --ink。
const HIGHLIGHT_INK = '#191a1c'
const LegacyHighlight = Highlight.extend({
  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-color') || element.style.backgroundColor || null,
        renderHTML: (attributes) => {
          if (!attributes.color) return {}
          return {
            'data-color': attributes.color,
            style: `background-color: ${attributes.color}; color: ${HIGHLIGHT_INK}`,
          }
        },
      },
    }
  },
})

export function richTextExtensions(): AnyExtension[] {
  return [
    // 高亮这几处有自己的一份（带颜色，下面的 LegacyHighlight）。实况文档的 `image` 节点按
    // 地址引外链图：这几处插的图一律是附件（下面的 attachmentImage），粘贴进来的外链图
    // 照旧不收；它只认 Markdown 里写的图，从 PDF 导入的题目描述靠它带着插图。
    ...docExtensions({ standalone: true }).flatMap((extension) => {
      if (extension.name === 'highlight') return []
      if (extension.name === 'image') return [(extension as Node).extend({ parseHTML: () => [] })]
      return [extension]
    }),
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    TextStyle,
    Color,
    FontFamily,
    LegacyFontSize,
    LegacyHighlight.configure({ multicolor: true }),
    Subscript,
    Superscript,
    AttachmentImage,
  ]
}

/** 存成 JSON 的那几处，什么都没写时存的就是这一份。 */
export const EMPTY_DOC: JSONContent = { type: 'doc', content: [{ type: 'paragraph' }] }

function isDoc(value: unknown): value is JSONContent {
  return typeof value === 'object' && value !== null && (value as JSONContent).type === 'doc'
}

/**
 * 存成 JSON 的那几处读进来的样子：空的、不是文档的，当成空文档；字符串先当 JSON 解，
 * 解不出文档就是早年存下的纯文本，整段放进一个段落（不当 HTML 解，免得 `<` 被吃掉）。
 */
export function jsonContent(value: unknown): JSONContent {
  if (typeof value === 'string') {
    if (!value.trim()) return EMPTY_DOC
    try {
      const parsed: unknown = JSON.parse(value)
      if (isDoc(parsed)) return jsonContent(parsed)
    } catch {
      // 不是 JSON：下面当纯文本。
    }
    return { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: value }] }] }
  }
  if (!isDoc(value) || !Array.isArray(value.content) || value.content.length === 0) return EMPTY_DOC
  return value
}

/** 只读那一侧什么都收：JSON 文档（对象或字符串）照 JSON 读，其余的字符串是 HTML。 */
export function viewerContent(value: unknown): JSONContent | string {
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)
      if (isDoc(parsed)) return jsonContent(parsed)
    } catch {
      // 不是 JSON：是 HTML。
    }
    return value
  }
  return jsonContent(value)
}

/** 存成 Markdown 的那一份（从 PDF 导入的题目描述）读成编辑器的文档。 */
export function markdownContent(markdown: string): JSONContent {
  return jsonContent(parseMarkdown(markdown).toJSON())
}
