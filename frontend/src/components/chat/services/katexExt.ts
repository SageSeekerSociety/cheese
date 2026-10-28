// 渲染 KaTeX 需要的样式跟着渲染代码走，不再由首屏全局带上。
import 'katex/dist/katex.min.css'

import katex, { type KatexOptions } from 'katex'
import { MarkedExtension, TokenizerAndRendererExtension } from 'marked'

import { isInlineMath } from '@/lib/docSchema/math'

export default function (options: KatexOptions = {}): MarkedExtension {
  return {
    extensions: [inlineKatex(options), blockKatex(options)],
  }
}

function inlineKatex(options: KatexOptions): TokenizerAndRendererExtension {
  const inlineOptions: KatexOptions = { ...options, displayMode: false }
  return {
    name: 'inlineKatex',
    level: 'inline',
    start(src: string) {
      return src.search(/(?<!\\)\$(?!\s)/)
    },
    tokenizer(src: string) {
      const doubleMatch = src.match(/^\$\$(?!\$)[ \t]*((?:\\.|(?!\$\$)[^\\\n`])+?)[ \t]*\$\$(?!\$)/)
      if (doubleMatch && doubleMatch[1].trim()) {
        return {
          type: 'inlineKatex',
          raw: doubleMatch[0],
          text: doubleMatch[1].trim(),
        }
      }
      const match = src.match(/^\$(?![\s$])((?:\\.|[^$\\\n`])+?)(?<!\s)\$(?!\d)/)
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
  const blockOptions: KatexOptions = { ...options, displayMode: true }
  return {
    name: 'blockKatex',
    level: 'block',
    start(src: string) {
      return src.indexOf('$$')
    },
    tokenizer(src: string) {
      const match = src.match(/^ {0,3}\$\$[ \t]*\n?((?:\\.|(?!\$\$)[\s\S])+?)\n?[ \t]*\$\$[ \t]*(?:\n|$)/)
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
