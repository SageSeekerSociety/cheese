// Whether the document's blocks are written in their own shape.
//
// writeCheck counts letters, and a block written in the wrong shape usually
// keeps every letter: a `:::timeline` with no closing line reads as a
// paragraph that starts with colons, a timeline item without its `|` puts the
// whole line under the time, a `[^2]` with no definition points at nothing.
// Each message names the line and gives the right form, so the writer can fix
// it without looking anything up.

import type { Token, Tokens } from 'marked'
import type { WriteProblem } from './writeCheck'

import { CHART_KINDS, chartInfo, chartNumber } from '../src/lib/docSchema/blocks'

const CONTAINERS: Record<string, string> = {
  timeline: '时间线',
  stats: '指标卡',
  columns: '分栏',
  column: '分栏',
  chart: '图表',
}
const CHART_FORM =
  '图表写成 :::chart 类型，下一行起放一张 Markdown 表格（第一列是分类，其余每列一组数），最后单独一行 :::'
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
          message: `第 ${at} 行的「:::${name}」不是文档支持的块。支持的是 :::timeline（时间线）、:::stats（指标卡）、::::columns（分栏）、:::chart（图表）。`,
        }
      }
      if (name === 'column') {
        return {
          line: at,
          message: `第 ${at} 行的「:::column」不在分栏里。分栏写成 ::::columns，里面放两到三个 :::column … :::，最后一行 ::::。`,
        }
      }
      if (name === 'chart') return chartHeadProblem(text, at)
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

/** A `:::chart` line that did not open a chart: its type, or what it holds. */
function chartHeadProblem(text: string, at: number): WriteProblem {
  const info = text.replace(/^\s*:{3,}chart/, '').trim()
  if (!chartInfo(info)) {
    return {
      line: at,
      message: `第 ${at} 行的图表类型「${info}」不对。类型是 ${CHART_KINDS.join('、')} 之一；只有 bar 和 stacked 能在后面加 horizontal（横着画）。`,
    }
  }
  return {
    line: at,
    message: `第 ${at} 行的图表没有读成块：里面只能有一张表格，前面要空一行。${CHART_FORM}。`,
  }
}

/** A chart whose table cannot be drawn: too few columns, or words where numbers go. */
function chartProblem(token: Token, line: number): WriteProblem | null {
  const table = (token as Tokens.Generic).tokens?.[0] as Tokens.Table | undefined
  if (!table) return null
  const kind = (token as Tokens.Generic).kind as string
  const head = token.raw.split('\n').findIndex((text) => text.trim().startsWith('|'))
  const width = table.header.length
  if (width < 2 || table.rows.length === 0) {
    return { line: line + head, message: `第 ${line + head} 行的图表至少要两列、一行数据。${CHART_FORM}。` }
  }
  if (kind === 'pie' && width !== 2) {
    return {
      line: line + head,
      message: `第 ${line + head} 行的饼图有 ${width} 列。饼图只画一组数：表格写两列，名称 | 数值。`,
    }
  }
  const from = kind === 'scatter' ? 0 : 1
  for (const [r, row] of table.rows.entries()) {
    for (let c = from; c < row.length; c++) {
      if (chartNumber(row[c].text) !== null) continue
      const at = line + head + 2 + r
      return {
        line: at,
        message: `第 ${at} 行图表数据「${row[c].text.trim()}」不是一个数。格子里只写数（可以带 , % 和货币符号），单位写进这一列的表头。`,
      }
    }
  }
  return null
}

// Mermaid kinds the document has its own block for: on a phone they are cut
// off, carry no value labels, and their legend colours do not match.
const MERMAID_REPLACED: Record<string, string> = {
  pie: '饼图改用图表块：:::chart pie，里面一张两列的表格（名称 | 数值）',
  'xychart-beta': '柱状图和折线图改用图表块：:::chart bar 或 :::chart line，里面一张表格，第一列是横轴，其余每列一组数',
  xychart: '柱状图和折线图改用图表块：:::chart bar 或 :::chart line，里面一张表格，第一列是横轴，其余每列一组数',
  timeline: '时间线改用时间线块：:::timeline，每项写成 - 时间 | 标题',
}

/** A mermaid diagram of a kind the document draws with its own block. */
function mermaidProblem(token: Tokens.Code, line: number): WriteProblem | null {
  if (token.lang?.trim().toLowerCase() !== 'mermaid') return null
  const lines = token.text.split('\n')
  let i = 0
  if (lines[0]?.trim() === '---') {
    i = lines.findIndex((text, n) => n > 0 && text.trim() === '---') + 1
    if (i === 0) return null
  }
  for (; i < lines.length; i++) {
    const text = lines[i].trim()
    if (!text || text.startsWith('%%')) continue
    const fix = MERMAID_REPLACED[text.split(/\s/)[0]]
    return fix ? { line: line + 1 + i, message: `第 ${line + 1 + i} 行的 mermaid ${fix}。` } : null
  }
  return null
}

/** The first block on this top-level token written in the wrong shape. */
export function blockProblem(token: Token, line: number): WriteProblem | null {
  if (token.type === 'timeline' || token.type === 'stats') return fieldsProblem(token.raw, line, token.type)
  if (token.type === 'chart') return chartProblem(token, line)
  if (token.type === 'code') return mermaidProblem(token as Tokens.Code, line)
  if (token.type === 'columns' || token.type === 'footnoteDef') return null
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
