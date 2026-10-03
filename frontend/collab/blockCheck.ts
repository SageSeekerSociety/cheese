// Whether the document's blocks are written in their own shape.
//
// writeCheck counts letters, and a block written in the wrong shape usually
// keeps every letter: a `:::timeline` with no closing line reads as a
// paragraph that starts with colons, a timeline item without its `|` puts the
// whole line under the time, a `[^2]` with no definition points at nothing.
// Each message names the line and gives the right form, so the writer can fix
// it without looking anything up.

import type { Token } from 'marked'
import type { WriteProblem } from './writeCheck'

const CONTAINERS: Record<string, string> = { timeline: '时间线', stats: '指标卡', columns: '分栏', column: '分栏' }
const CONTAINER_LINE = /^\s*(:{3,})([a-z]*)/
const FOOTNOTE_DEF_LINE = /^ {0,3}\[\^[^\]\s^]+\]:/
const ITEM_LINE = /^[-*+][ \t]+/

function shown(line: string): string {
  const flat = line.trim()
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat
}

/** A `:::` or footnote line the parser did not read as its block. */
function strayProblem(raw: string, line: number): WriteProblem | null {
  for (const [i, text] of raw.split('\n').entries()) {
    const at = line + i
    const container = CONTAINER_LINE.exec(text)
    if (container) {
      const name = container[2]
      if (!name) {
        return { line: at, message: `第 ${at} 行多了一个「${container[1]}」，前面没有和它配对的开头。` }
      }
      if (!CONTAINERS[name]) {
        return {
          line: at,
          message: `第 ${at} 行的「:::${name}」不是文档支持的块。支持的是 :::timeline（时间线）、:::stats（指标卡）、::::columns（分栏）。`,
        }
      }
      if (name === 'column') {
        return {
          line: at,
          message: `第 ${at} 行的「:::column」不在分栏里。分栏写成 ::::columns，里面放两到三个 :::column … :::，最后一行 ::::。`,
        }
      }
      const columns = name === 'columns' ? '，里面是两到三个 :::column … :::' : ''
      return {
        line: at,
        message: `第 ${at} 行的${CONTAINERS[name]}没有读成块：开头一行前面要空一行，结尾要有单独一行「${container[1]}」${columns}。`,
      }
    }
    if (FOOTNOTE_DEF_LINE.test(text)) {
      return { line: at, message: `第 ${at} 行的脚注定义（${shown(text)}）要单独成段：前面空一行，写成 [^1]: 说明。` }
    }
  }
  return null
}

/** An item of a timeline or a stat card without its `|`. */
function fieldsProblem(raw: string, line: number, kind: 'timeline' | 'stats'): WriteProblem | null {
  for (const [i, text] of raw.split('\n').entries()) {
    if (i === 0 || CONTAINER_LINE.test(text) || !text.trim()) continue
    const item = kind === 'stats' || ITEM_LINE.test(text)
    if (item && !text.includes('|')) {
      const form = kind === 'stats' ? '- 名称 | 数值 | 变化（变化可以不写）' : '- 时间 | 标题，说明写在下一行并缩进两格'
      return { line: line + i, message: `第 ${line + i} 行（${shown(text)}）缺少「|」。每一项写成 ${form}。` }
    }
  }
  return null
}

/** The first block on this top-level token written in the wrong shape. */
export function blockProblem(token: Token, line: number): WriteProblem | null {
  if (token.type === 'timeline' || token.type === 'stats') return fieldsProblem(token.raw, line, token.type)
  if (token.type === 'columns' || token.type === 'code' || token.type === 'footnoteDef') return null
  return strayProblem(token.raw, line)
}

/** A footnote referenced in the text with no definition anywhere. */
export function footnoteProblem(markdown: string): WriteProblem | null {
  const prose = markdown.replace(/^(```|~~~)[\s\S]*?^\1/gm, (code) => code.replace(/[^\n]/g, ' '))
  const defined = new Set([...prose.matchAll(/^ {0,3}\[\^([^\]\s^]+)\]:/gm)].map((m) => m[1]))
  for (const [i, text] of prose.split('\n').entries()) {
    for (const ref of text.matchAll(/\[\^([^\]\s^]+)\](?!:)/g)) {
      if (!defined.has(ref[1])) {
        return {
          line: i + 1,
          message: `第 ${i + 1} 行引用了脚注 [^${ref[1]}]，文档里没有它的定义。在文末单独一行写 [^${ref[1]}]: 说明。`,
        }
      }
    }
  }
  return null
}
