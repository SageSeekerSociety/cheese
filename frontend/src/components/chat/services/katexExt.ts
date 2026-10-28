// 渲染 KaTeX 需要的样式跟着渲染代码走，不再由首屏全局带上。
import 'katex/dist/katex.min.css'

import katex, { type KatexOptions } from 'katex'
import { MarkedExtension, TokenizerAndRendererExtension } from 'marked'

import { BLOCK_MATH_RE, INLINE_DOUBLE_MATH_RE, INLINE_SINGLE_MATH_RE, isInlineMath } from '@/lib/docSchema/math'

export default function (options: KatexOptions = {}): MarkedExtension {
  return {
    extensions: [inlineKatex(options), blockKatex(options)],
  }
}

function inlineKatex(options: KatexOptions): TokenizerAndRendererExtension {
  const inlineOptions: KatexOptions = { throwOnError: false, ...options, displayMode: false }
  return {
    name: 'inlineKatex',
    level: 'inline',
    start(src: string) {
      return src.search(/(?<!\\)\$(?!\s)/)
    },
    tokenizer(src: string) {
      const doubleMatch = src.match(INLINE_DOUBLE_MATH_RE)
      if (doubleMatch && isInlineMath(doubleMatch[1].trim())) {
        return {
          type: 'inlineKatex',
          raw: doubleMatch[0],
          text: doubleMatch[1].trim(),
        }
      }
      const match = src.match(INLINE_SINGLE_MATH_RE)
      if (match && isInlineMath(match[1])) {
        return {
          type: 'inlineKatex',
          raw: match[0],
          text: match[1].trim(),
        }
      }
    },
    renderer(token) {
      return katex.renderToString(token.text, inlineOptions)
    },
  }
}

function blockKatex(options: KatexOptions): TokenizerAndRendererExtension {
  const blockOptions: KatexOptions = { throwOnError: false, ...options, displayMode: true }
  return {
    name: 'blockKatex',
    level: 'block',
    start(src: string) {
      return src.indexOf('$$')
    },
    tokenizer(src: string) {
      const match = src.match(BLOCK_MATH_RE)
      if (match) {
        return {
          type: 'blockKatex',
          raw: match[0],
          text: match[1].trim(),
        }
      }
    },
    renderer(token) {
      return `<p>${katex.renderToString(token.text, blockOptions)}</p>`
    },
  }
}
