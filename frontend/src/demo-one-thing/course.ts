// The course space 「计算机系统基础（ICS 2026）」: the 题目 the teacher published
// in it, what each 题目 brings into the project that 领取 creates, the judges,
// and the space's 公告 and 资料库. A 题目 is not a kind of screen: once claimed
// it is an ordinary project owned by a team (the person's own team, or the team
// that claimed it together), holding the 题面 as a document, the starter code,
// the rules in its settings, and one 一件事 the work is done in (WORK_TITLE:
// 「做 Data Lab」, done by the person, or by the whole team). The project records which 题目 it came from. A
// submission is made to the 题目, not inside the project: it carries the
// 题目's 提交项 (a version of a project file, a few words), is graded the way
// the 题目 says (评测机评 or 老师评) and is recorded on the 题目 in the space.
// A space has no channels.
//
// This is the student's side only. The teacher's views (the class's
// submissions, the grade sheet, writing a 题目 and its tests) exist in the
// product and are not part of this demo.
import type { ChatLine } from './state'

import { reactive } from 'vue'

import { type ClaimKey, dayAt, NOW } from './data'

export interface JudgeRow {
  name: string
  ok: boolean
  ms: number
  note?: string
}

/** What the judge says about one version of a judged unit. */
export interface Verdict {
  score: number
  total: number
  rows: JudgeRow[]
  /** One line under the score: what passed, or what failed. */
  summary: string
}

/** 张老师's review of a submission to a 老师评 题目 (the product's TaskSubmissionReview). */
export interface Review {
  score: number
  total: number
  comment: string
  by: string
  at: string
}

/** One submission to a 题目 (the product's TaskSubmission): what it carried and what it got. */
export interface Submission {
  id: string
  /** The 题目 it was made to. */
  topic: string
  /** What is graded: the 题目 itself, or one problem of the 题单 ('P1001'). */
  unit: string
  /** 第 n 次提交 of this unit. */
  n: number
  at: string
  who: string
  /** The team it was submitted as, for a team 题目. */
  team?: string
  /** The FILE item: the project file, the version's short id and its text, so anyone can see which code was graded. */
  path: string
  version: string
  code: string
  /** The TEXT items, as written. */
  texts: { prompt: string; text: string }[]
  /** 评测机评: the judge's verdict, once it is back. */
  verdict?: Verdict
  /** 老师评: the teacher's review, once it is written. */
  review?: Review
}

const daysAhead = (d: number, h: number, m: number) => dayAt(-d, h, m)
const int = (n: number) => n | 0

/** A version's short id: the same text always gets the same id. */
export function versionOf(text: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return (h >>> 0).toString(16).padStart(8, '0').slice(0, 6)
}

// ---- the space's 题目 ----

export const CATEGORIES = ['Lab', '题单', '大作业'] as const
export type Category = (typeof CATEGORIES)[number]

/** How a 题目's submissions are graded: by the judge, at once, or by the teacher, who gives a score and a comment. */
export type Grading = 'judge' | 'teacher'
export const GRADING_TEXT: Record<Grading, string> = { judge: '评测机评', teacher: '老师评' }
export const GRADING_NOTE: Record<Grading, string> = {
  judge: '提交后评测机马上出结果',
  teacher: '张老师批改后给分和评语',
}

/** One 提交项 (the product's TaskSubmissionSchemaEntry): a file of the project, or a few words typed when submitting. */
export interface SubmitItem {
  type: 'FILE' | 'TEXT'
  prompt: string
  /** FILE: the project file whose version is submitted. */
  path?: string
  /** TEXT: what the box starts with. */
  draft?: string
}

export interface Topic {
  id: string
  title: string
  category: Category
  about: string
  due: string
  published: string
  /** Who claims it: one person (into their own team), a team of `size`, or either. */
  mode: 'solo' | 'team' | 'either'
  size?: number
  /** The project 领取 creates; a 题目 without one is shown but not claimable in the demo. */
  project?: ClaimKey
  grading: Grading
  /** 提交项. A 题单 has none of its own: each problem is submitted alone (see submitItems). */
  items: SubmitItem[]
}

export const TOPICS: Topic[] = [
  {
    id: 'datalab',
    title: 'Data Lab',
    category: 'Lab',
    about: '在 bits.c 里用位运算写 13 个函数，一起评分',
    due: daysAhead(3, 23, 59),
    published: dayAt(6, 9, 0),
    mode: 'solo',
    project: 'datalab',
    grading: 'judge',
    items: [{ type: 'FILE', prompt: 'bits.c', path: 'bits.c' }],
  },
  {
    id: 'week3',
    title: '第 3 周题单',
    category: '题单',
    about: '5 道小题：输入输出、分支和循环，每题单独评测',
    due: daysAhead(5, 23, 59),
    published: dayAt(2, 8, 30),
    mode: 'solo',
    project: 'week3',
    grading: 'judge',
    items: [],
  },
  {
    id: 'shelllab',
    title: 'Shell Lab 大作业（3 人组队）',
    category: '大作业',
    about: '三个人一起写一个带作业控制的 shell：tsh.c，8 条 trace 评分',
    due: daysAhead(10, 23, 59),
    published: dayAt(1, 10, 10),
    mode: 'team',
    size: 3,
    project: 'shelllab',
    grading: 'teacher',
    items: [
      { type: 'FILE', prompt: 'tsh.c', path: 'tsh.c' },
      {
        type: 'TEXT',
        prompt: '分工说明',
        draft:
          '林晓：eval（fork、execve、addjob）\n周珂：内置命令 jobs / bg / fg\n陈思远：sigchld_handler 和 sigint_handler',
      },
    ],
  },
  {
    id: 'cachelab',
    title: 'Cache Lab',
    category: 'Lab',
    about: '写一个缓存模拟器，再写一个对缓存友好的矩阵转置',
    due: daysAhead(12, 23, 59),
    published: dayAt(1, 10, 5),
    mode: 'either',
    size: 2,
    grading: 'judge',
    items: [
      { type: 'FILE', prompt: 'csim.c', path: 'csim.c' },
      { type: 'FILE', prompt: 'trans.c', path: 'trans.c' },
    ],
  },
  {
    id: 'bomblab',
    title: 'Bomb Lab',
    category: 'Lab',
    about: '用 gdb 读汇编，拆掉六关炸弹',
    due: daysAhead(19, 23, 59),
    published: dayAt(1, 10, 6),
    mode: 'solo',
    grading: 'judge',
    items: [{ type: 'TEXT', prompt: '六关的拆弹字符串' }],
  },
]

/** 个人 / 组队 · 3 人 / 个人或 2 人组队: how a 题目 is claimed. */
export const modeText = (t: Topic) =>
  t.mode === 'solo' ? '个人' : t.mode === 'team' ? `组队 · ${t.size} 人` : `个人或 ${t.size} 人组队`

export const topicById = (id: string) => TOPICS.find((t) => t.id === id)
export const topicOf = (p: ClaimKey) => TOPICS.find((t) => t.project === p)!

/** Demo-only clock for deadlines: the top bar's 「模拟：离截止不到 24 小时」. Not stored, not in the link. */
export const CLOCK = reactive({ dueSoon: false })

/** The time deadlines are read against. 「模拟：离截止不到 24 小时」 moves it to 20 hours before
 *  Data Lab's deadline (the nearest one), so every countdown moves with it; nothing else reads it. */
export function dueNow(): number {
  return CLOCK.dueSoon ? Date.parse(TOPICS[0]!.due) - 20 * 3_600_000 : NOW
}

/** Whole hours left before a deadline (negative once past). */
export const hoursLeft = (iso: string) => Math.floor((Date.parse(iso) - dueNow()) / 3_600_000)

/** 「3 天后截止」 for a 题目 due within a week, else nothing. */
export function dueChip(iso: string): string | null {
  const days = Math.floor((Date.parse(iso) - dueNow()) / 86_400_000)
  if (days > 7 || days < 0) return null
  return days === 0 ? '今天截止' : days === 1 ? '明天截止' : `${days} 天后截止`
}

// ---- Data Lab: 13 functions in bits.c, judged together ----

/** A C function the demo judges for real: its expression is parsed and run on the tests. */
interface Puzzle {
  name: string
  args: string[]
  legal: string[]
  maxOps: number
  rating: number
  ref: (...a: number[]) => number
  tests: number[][]
  /** The comment above the function in bits.c. */
  doc: string
}

const ALL = ['!', '~', '&', '^', '|', '+', '<<', '>>']
const popcount = (x: number) => {
  let n = 0
  for (let v = x >>> 0; v; v >>>= 1) n += v & 1
  return n
}
const howMany = (x: number) => {
  let v = x < 0 ? ~x : x
  let b = 0
  for (; v; v >>>= 1) b++
  return b + 1
}

export const PUZZLES: Puzzle[] = [
  {
    name: 'bitXor',
    args: ['x', 'y'],
    legal: ['~', '&'],
    maxOps: 14,
    rating: 1,
    ref: (x, y) => int(x ^ y),
    tests: [
      [4, 5],
      [0, 0],
      [-1, 0x7fffffff],
      [0x55555555, int(0xaaaaaaaa)],
    ],
    doc: 'x^y using only ~ and &\n *   Example: bitXor(4, 5) = 1',
  },
  {
    name: 'tmin',
    args: [],
    legal: ALL,
    maxOps: 4,
    rating: 1,
    ref: () => -2147483648,
    tests: [[]],
    doc: "return minimum two's complement integer",
  },
  {
    name: 'isTmax',
    args: ['x'],
    legal: ['!', '~', '&', '^', '|', '+'],
    maxOps: 10,
    rating: 1,
    ref: (x) => (x === 0x7fffffff ? 1 : 0),
    tests: [[0x7fffffff], [-1], [0], [-2147483648]],
    doc: "returns 1 if x is the maximum, two's complement number,\n *     and 0 otherwise",
  },
  {
    name: 'allOddBits',
    args: ['x'],
    legal: ALL,
    maxOps: 12,
    rating: 2,
    ref: (x) => ((x & int(0xaaaaaaaa)) === int(0xaaaaaaaa) ? 1 : 0),
    tests: [[int(0xfffffffd)], [int(0xaaaaaaaa)], [-1], [0]],
    doc: 'return 1 if all odd-numbered bits in word set to 1\n *   Examples allOddBits(0xFFFFFFFD) = 0, allOddBits(0xAAAAAAAA) = 1',
  },
  {
    name: 'negate',
    args: ['x'],
    legal: ALL,
    maxOps: 5,
    rating: 2,
    ref: (x) => int(-x),
    tests: [[1], [0], [-5], [-2147483648]],
    doc: 'return -x\n *   Example: negate(1) = -1.',
  },
  {
    name: 'getByte',
    args: ['x', 'n'],
    legal: ALL,
    maxOps: 6,
    rating: 2,
    ref: (x, n) => (x >>> (n! * 8)) & 0xff,
    tests: [
      [0x12345678, 1],
      [int(0x87654321), 3],
      [-1, 0],
      [0, 2],
    ],
    doc: 'Extract byte n from word x\n *   Bytes numbered from 0 (LSB) to 3 (MSB)\n *   Examples: getByte(0x12345678,1) = 0x56',
  },
  {
    name: 'isAsciiDigit',
    args: ['x'],
    legal: ALL,
    maxOps: 15,
    rating: 3,
    ref: (x) => (x >= 0x30 && x <= 0x39 ? 1 : 0),
    tests: [[0x35], [0x3a], [0x05], [0x30]],
    doc: "return 1 if 0x30 <= x <= 0x39 (ASCII codes for characters '0' to '9')\n *   Example: isAsciiDigit(0x35) = 1.",
  },
  {
    name: 'conditional',
    args: ['x', 'y', 'z'],
    legal: ALL,
    maxOps: 16,
    rating: 3,
    ref: (x, y, z) => (x ? y! : z!),
    tests: [
      [2, 4, 5],
      [0, 4, 5],
      [-1, 7, 9],
      [0, -3, 8],
    ],
    doc: 'same as x ? y : z\n *   Example: conditional(2,4,5) = 4',
  },
  {
    name: 'isLessOrEqual',
    args: ['x', 'y'],
    legal: ALL,
    maxOps: 24,
    rating: 3,
    ref: (x, y) => (x <= y! ? 1 : 0),
    tests: [
      [4, 5],
      [5, 4],
      [-2147483648, 2147483647],
      [2147483647, -2147483648],
    ],
    doc: 'if x <= y  then return 1, else return 0\n *   Example: isLessOrEqual(4,5) = 1.',
  },
  {
    name: 'logicalShift',
    args: ['x', 'n'],
    legal: ALL,
    maxOps: 20,
    rating: 3,
    ref: (x, n) => int(x >>> n!),
    tests: [
      [int(0x87654321), 4],
      [-1, 31],
      [0x12345678, 0],
      [-2147483648, 1],
    ],
    doc: 'shift x to the right by n, using a logical shift\n *   Examples: logicalShift(0x87654321,4) = 0x08765432',
  },
  {
    name: 'logicalNeg',
    args: ['x'],
    legal: ['~', '&', '^', '|', '+', '<<', '>>'],
    maxOps: 12,
    rating: 4,
    ref: (x) => (x === 0 ? 1 : 0),
    tests: [[0], [3], [-2147483648], [2147483647]],
    doc: 'implement the ! operator, using all of\n *              the legal operators except !\n *   Examples: logicalNeg(3) = 0, logicalNeg(0) = 1',
  },
  {
    name: 'bitCount',
    args: ['x'],
    legal: ALL,
    maxOps: 40,
    rating: 4,
    ref: (x) => popcount(x),
    tests: [[5], [7], [-1], [0]],
    doc: "returns count of number of 1's in word\n *   Examples: bitCount(5) = 2, bitCount(7) = 3",
  },
  {
    name: 'howManyBits',
    args: ['x'],
    legal: ALL,
    maxOps: 90,
    rating: 4,
    ref: (x) => howMany(x),
    tests: [[12], [298], [-5], [0]],
    doc: "return the minimum number of bits required to represent x in\n *             two's complement\n *  Examples: howManyBits(12) = 5, howManyBits(-5) = 4",
  },
]

export const DATALAB_TOTAL = PUZZLES.reduce((n, p) => n + p.rating, 0)

const signature = (p: Puzzle) => `int ${p.name}(${p.args.length ? p.args.map((a) => `int ${a}`).join(', ') : 'void'})`

const BITS_C = `/*
 * CS:APP Data Lab
 *
 * 林晓
 *
 * bits.c - 每个函数只能用一行或几行 int 变量和 return，
 *          只能用注释里列出的运算符，常量只能是 0 到 255（0xFF）。
 *          不能用 if、循环、函数调用和宏。
 */
${PUZZLES.map(
  (p) => `
/*
 * ${p.name} - ${p.doc}
 *   Legal ops: ${p.legal.join(' ')}
 *   Max ops: ${p.maxOps}
 *   Rating: ${p.rating}
 */
${signature(p)} {
  return 2;
}`
).join('\n')}
`

const DATALAB_README = `<h1>Data Lab</h1>
<p>计算机系统基础（ICS 2026） · 张老师发布。这个 Lab 练的是整数和位运算：在 <code>bits.c</code> 里把 13 个函数写完，每个函数只能用规定的运算符，运算符个数也有上限。</p>
<h3>规则</h3>
<ul><li><p>函数体里只能写 <code>int</code> 变量和 <code>return</code>，不能用 if、循环、函数调用和宏。</p></li><li><p>只能用函数注释里列出的运算符（Legal ops），个数不超过 Max ops。</p></li><li><p>常量只能是 0 到 255（<code>0xFF</code>）。</p></li></ul>
<h3>评分</h3>
<p>13 个函数一起评：每个函数的测试全对、且 <code>dlc</code> 检查通过，就得它的分（Rating），满分 ${DATALAB_TOTAL} 分。可以反复提交，以截止前最后一次为准。</p>
<h3>怎么做</h3>
<ul><li><p>领取时开好了一件事「做 Data Lab」：在它的工作台里写 <code>bits.c</code>，「运行」跑 <code>./btest</code>，输出留在那件事里。</p></li><li><p>芝士在那件事的对话里只给提示，不写代码。</p></li><li><p>做好了在这道题目上「提交」：挑 <code>bits.c</code> 的一个版本交上去，评测机马上出结果。提交记录也在这道题目上，每次都能看到当时的代码。</p></li></ul>`

// ---- the judge: a C integer expression, parsed and run ----
//
// Data Lab answers are one function of `int`s whose body is a few `int a = …;`
// lines and a `return …;`, built only from bit operators. That small language is
// parsed and evaluated here on 32-bit ints, so any correct answer passes, not
// only the one in the hint, and a wrong one fails on the test that shows it.

type Ast =
  | { k: 'num'; v: number }
  | { k: 'var'; name: string }
  | { k: 'un'; op: string; a: Ast }
  | { k: 'bin'; op: string; a: Ast; b: Ast }

const LEVELS = [['|'], ['^'], ['&'], ['<<', '>>'], ['+', '-']]

function parse(src: string): { ast: Ast; ops: string[]; consts: string[] } {
  const toks = src.match(/0[xX][0-9a-fA-F]+|\d+|[A-Za-z_]\w*|<<|>>|[~!&^|+\-()]|\S/g) ?? []
  let i = 0
  const ops: string[] = []
  const consts: string[] = []
  const peek = () => toks[i]
  const unary = (): Ast => {
    const t = toks[i++]
    if (t === undefined) throw new Error('表达式不完整')
    if (t === '~' || t === '!' || t === '-') {
      ops.push(t)
      return { k: 'un', op: t, a: unary() }
    }
    if (t === '(') {
      const e = level(0)
      if (toks[i++] !== ')') throw new Error("缺少 ')'")
      return e
    }
    if (/^(0[xX][0-9a-fA-F]+|\d+)$/.test(t)) {
      consts.push(t)
      return { k: 'num', v: int(Number(t)) }
    }
    if (/^[A-Za-z_]\w*$/.test(t)) return { k: 'var', name: t }
    throw new Error(`'${t}' 前面应该是一个表达式`)
  }
  const level = (n: number): Ast => {
    if (n === LEVELS.length) return unary()
    let a = level(n + 1)
    while (peek() !== undefined && LEVELS[n]!.includes(peek()!)) {
      const op = toks[i++]!
      ops.push(op)
      a = { k: 'bin', op, a, b: level(n + 1) }
    }
    return a
  }
  const ast = level(0)
  if (i < toks.length) throw new Error(`'${toks[i]}' 前面少了运算符`)
  return { ast, ops, consts }
}

function evaluate(e: Ast, env: Record<string, number>): number {
  switch (e.k) {
    case 'num':
      return e.v
    case 'var':
      if (!(e.name in env)) throw new Error(`'${e.name}' 没有声明`)
      return env[e.name]!
    case 'un': {
      const a = evaluate(e.a, env)
      return e.op === '~' ? ~a : e.op === '!' ? (a === 0 ? 1 : 0) : int(-a)
    }
    default: {
      const a = evaluate(e.a, env)
      const b = evaluate(e.b, env)
      const ops: Record<string, number> = {
        '|': a | b,
        '^': a ^ b,
        '&': a & b,
        '<<': a << b,
        '>>': a >> b,
        '+': int(a + b),
        '-': int(a - b),
      }
      return ops[e.op]!
    }
  }
}

interface Compiled {
  call: (args: number[]) => number
  ops: string[]
  consts: string[]
  line: number
}

const stripComments = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' ')).replace(/\/\/.*$/gm, '')

/** Find one function in bits.c and compile its body; a C-compiler-like message on failure. */
function compile(code: string, fn: Puzzle): Compiled {
  const head = new RegExp(`int\\s+${fn.name}\\s*\\(([^)]*)\\)\\s*\\{`).exec(code)
  if (!head) throw new Error(`bits.c: 找不到函数 ${fn.name}`)
  const line = code.slice(0, head.index).split('\n').length
  const close = code.indexOf('}', head.index)
  if (close < 0) throw new Error(`bits.c:${line}: 函数 ${fn.name} 少了 '}'`)
  const statements = code
    .slice(head.index + head[0].length, close)
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
  const steps: { name: string | null; p: ReturnType<typeof parse> }[] = []
  for (const s of statements) {
    const decl = /^int\s+([A-Za-z_]\w*)\s*=\s*([\s\S]+)$/.exec(s)
    const ret = /^return\s+([\s\S]+)$/.exec(s)
    try {
      if (decl) steps.push({ name: decl[1]!, p: parse(decl[2]!) })
      else if (ret) steps.push({ name: null, p: parse(ret[1]!) })
      else throw new Error(`不认识的语句 '${s.split('\n')[0]}'（这里只能写 int 变量和 return）`)
    } catch (e) {
      throw new Error(`bits.c:${line}: ${fn.name}: ${(e as Error).message}`)
    }
  }
  if (steps.at(-1)?.name !== null) throw new Error(`bits.c:${line}: ${fn.name}: 函数最后要 return 一个值`)
  return {
    line,
    ops: steps.flatMap((s) => s.p.ops),
    consts: steps.flatMap((s) => s.p.consts),
    call: (args) => {
      const env: Record<string, number> = Object.fromEntries(fn.args.map((a, i) => [a, int(args[i]!)]))
      for (const s of steps) {
        const v = evaluate(s.p.ast, env)
        if (s.name === null) return v
        env[s.name] = v
      }
      return 0
    },
  }
}

const callText = (fn: Puzzle, args: number[]) => `${fn.name}(${args.join(', ')})`

/** One function's tests and dlc's operator and constant checks. */
function grade(code: string, fn: Puzzle): { ok: boolean; note?: string; ops?: number } {
  let c: Compiled
  try {
    c = compile(code, fn)
  } catch (e) {
    return { ok: false, note: (e as Error).message }
  }
  for (const args of fn.tests) {
    const want = fn.ref(...args)
    let got: number | string
    try {
      got = c.call(args)
    } catch (e) {
      got = (e as Error).message
    }
    if (got !== want) return { ok: false, note: `${callText(fn, args)} 应该是 ${want}，你的结果是 ${got}` }
  }
  const illegal = c.ops.find((o) => !fn.legal.includes(o))
  if (illegal) return { ok: false, note: `dlc：bits.c:${c.line}: 不能用运算符 ${illegal}` }
  const big = c.consts.find((k) => Number(k) > 255)
  if (big) return { ok: false, note: `dlc：bits.c:${c.line}: 常量 ${big} 超过 0xFF` }
  if (c.ops.length > fn.maxOps) return { ok: false, note: `dlc：用了 ${c.ops.length} 个运算符，上限 ${fn.maxOps}` }
  return { ok: true, ops: c.ops.length }
}

function judgeBits(text: string): Verdict {
  const code = stripComments(text)
  let score = 0
  let passed = 0
  const rows = PUZZLES.map((p, i) => {
    const g = grade(code, p)
    if (g.ok) {
      score += p.rating
      passed++
    }
    return {
      name: `${p.name}（${p.rating} 分）`,
      ok: g.ok,
      ms: (2 + (i % 4)) / 10,
      note: g.ok ? `用了 ${g.ops} 个运算符，上限 ${p.maxOps}` : g.note,
    }
  })
  return { score, total: DATALAB_TOTAL, rows, summary: `${score}/${DATALAB_TOTAL} · 13 个函数里通过 ${passed} 个` }
}

/** 运行 bits.c: btest on the copy as it now reads, in my own environment. */
function runBits(text: string): { cmd: string; out: string } {
  const code = stripComments(text)
  const out = PUZZLES.map((p) => {
    const g = grade(code, p)
    return `${g.ok ? '✓' : '✗'} ${p.name.padEnd(14)}${g.ok ? '' : g.note}`
  })
  const v = judgeBits(text)
  return { cmd: 'make btest && ./btest', out: `${out.join('\n')}\nTotal points: ${v.score}/${v.total}` }
}

// ---- 第 3 周题单: five small problems, each judged on its own ----

interface SetProblem {
  id: string
  title: string
  statement: string
  sampleIn: string
  sampleOut: string
  /** A second sample, and what the starter's usual mistake prints on it (the demo runs no real compiler). */
  sample2: { in: string; out: string; got: string }
  starter: string
  /** The demo's judge for this problem: the verdict on a version of main.cpp. */
  judge: (code: string) => Verdict
}

/** What 运行 feeds a 题单 problem: one of its 样例, or input typed by hand. */
export type RunInput = { sample: number } | { custom: string }

/** What a 运行 printed; on a 样例, also what was expected and whether it matched. */
export interface RunOut {
  cmd: string
  out: string
  expect?: string
  pass?: boolean
}

/** main.cpp run on one input. A+B really adds what it reads; the other problems are scripted on their two
 *  样例 (sample 1 comes out right, sample 2 shows the mistake the judge reports), and say so for other input. */
function runOn(p: SetProblem, code: string, input: string): string {
  if (!printsAnything(code)) return '(没有输出)'
  if (p.id === 'P1001') {
    if (!printsSum(code)) return '(输出不是 a + b)'
    const [a, b] = input.trim().split(/\s+/).map(Number)
    return Number.isFinite(a) && Number.isFinite(b) ? String(a! + b!) : '(读不到两个整数)'
  }
  if (input.trim() === p.sampleIn.trim()) return p.sampleOut
  if (input.trim() === p.sample2.in.trim()) return p.sample2.got
  return '(演示里这道题只模拟了两个样例的输出)'
}

/** 运行 on a 题单 problem: a 样例 shows 通过 / 不通过 against its expected output; custom input shows the output only. */
function runSet(p: SetProblem, code: string, input: RunInput = { sample: 0 }): RunOut {
  if ('custom' in input)
    return { cmd: 'g++ -O2 main.cpp -o main && ./main < 自定义输入', out: runOn(p, code, input.custom) }
  const s = input.sample === 1 ? { in: p.sample2.in, out: p.sample2.out } : { in: p.sampleIn, out: p.sampleOut }
  const out = runOn(p, code, s.in)
  return {
    cmd: `g++ -O2 main.cpp -o main && ./main < 样例${input.sample + 1}.in`,
    out,
    expect: s.out,
    pass: out === s.out,
  }
}

const CPP_HEAD = '#include <iostream>\nusing namespace std;\n\nint main() {\n'

/** Does main.cpp print `a + b`? (The demo reads the statement that writes the answer.) */
const printsSum = (code: string) => /cout\s*<<\s*\(?\s*(a\s*\+\s*b|b\s*\+\s*a)\b/.test(stripComments(code))
const printsAnything = (code: string) => /\b(cout\s*<<|printf\s*\()/.test(stripComments(code))

/** A problem the demo does not run for real: no output fails every test, any output gets the same partial result. */
function scriptedJudge(note: string): (code: string) => Verdict {
  return (code) => {
    if (!printsAnything(code))
      return {
        score: 0,
        total: 4,
        rows: [1, 2, 3, 4].map((i) => ({ name: `测试点 #${i}`, ok: false, ms: 1, note: '没有输出' })),
        summary: '0/4 · 程序没有输出',
      }
    return {
      score: 2,
      total: 4,
      rows: [1, 2, 3, 4].map((i) => ({ name: `测试点 #${i}`, ok: i < 3, ms: 2 + i, note: i < 3 ? undefined : note })),
      summary: `2/4 · 测试点 #3 答案错误`,
    }
  }
}

export const SET: SetProblem[] = [
  {
    id: 'P1001',
    title: 'A+B Problem',
    statement: '输入两个整数 a、b，输出它们的和。|a|, |b| ≤ 10⁹。',
    sampleIn: '20 30',
    sampleOut: '50',
    sample2: { in: '-7 7', out: '0', got: '0' },
    starter: `${CPP_HEAD}  int a, b;\n  cin >> a >> b;\n  // 在这里输出 a + b\n  return 0;\n}\n`,
    judge: (code) => {
      const tests: [string, number][] = [
        ['20 30', 50],
        ['1 2', 3],
        ['-7 7', 0],
        ['1000000000 -999999999', 1],
      ]
      const ok = printsSum(code)
      const rows = tests.map(([input, want], i) => ({
        name: `测试点 #${i + 1}（${input}）`,
        ok,
        ms: 1 + (i % 2),
        note: ok ? undefined : printsAnything(code) ? `期望 ${want}，输出不一样` : `期望 ${want}，程序没有输出`,
      }))
      const score = ok ? 4 : 0
      return { score, total: 4, rows, summary: ok ? '4/4 · 全部通过' : `0/4 · 测试点 #1 ${rows[0]!.note}` }
    },
  },
  {
    id: 'P1046',
    title: '陶陶摘苹果',
    statement: '树上 10 个苹果的高度，和陶陶伸手能摸到的高度。陶陶站上 30 厘米的板凳也能摘，问能摘到几个。',
    sampleIn: '100 200 150 140 129 134 167 198 200 111\n110',
    sampleOut: '5',
    sample2: { in: '100 200 150 140 129 134 167 198 200 111\n140', out: '7', got: '5' },
    starter: `${CPP_HEAD}  int h[10], reach;\n  for (int i = 0; i < 10; i++) cin >> h[i];\n  cin >> reach;\n  // 数一数能摘到几个\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 7，你的输出是 5（板凳的 30 厘米算进去了吗）'),
  },
  {
    id: 'P1085',
    title: '不高兴的津津',
    statement: '一周七天，每天上学和补习的小时数。超过 8 小时津津就不高兴，输出最不高兴的那天，都高兴输出 0。',
    sampleIn: '5 3\n6 2\n7 2\n5 3\n5 4\n0 4\n0 6',
    sampleOut: '3',
    sample2: { in: '5 3\n6 4\n7 2\n5 3\n5 5\n0 4\n0 6', out: '2', got: '5' },
    starter: `${CPP_HEAD}  // 读 7 行，每行两个数\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 2，你的输出是 5（一样不高兴时输出最早的那天）'),
  },
  {
    id: 'P1089',
    title: '津津的储蓄计划',
    statement: '每月初拿到 300 元，预算花完后把整百的钱存给妈妈，年底多加 20%。某月不够花就输出 -那个月。',
    sampleIn: '290\n230\n280\n200\n300\n170\n340\n50\n90\n80\n200\n60',
    sampleOut: '-7',
    sample2: { in: '290\n230\n280\n200\n300\n170\n330\n50\n90\n80\n200\n60', out: '1580', got: '1320' },
    starter: `${CPP_HEAD}  int budget[12];\n  for (int i = 0; i < 12; i++) cin >> budget[i];\n  // 按月模拟\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 1580，你的输出是 1320（年底的 20% 加了吗）'),
  },
  {
    id: 'P5703',
    title: '苹果采购',
    statement: '每个苹果 a 元，要买 b 个，输出一共多少元。',
    sampleIn: '5 3',
    sampleOut: '15',
    sample2: { in: '1000000 1000000', out: '1000000000000', got: '-727379968' },
    starter: `${CPP_HEAD}  long long a, b;\n  cin >> a >> b;\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 1000000000000，你的输出溢出了'),
  },
]

export const setProblem = (id: string) => SET.find((p) => p.id === id)

const setStatement = (p: SetProblem) => `<h2>${p.id} ${p.title}</h2>
<p>${p.statement}</p>
<h3>样例输入</h3>
<pre><code>${p.sampleIn}</code></pre>
<h3>样例输出</h3>
<pre><code>${p.sampleOut}</code></pre>
<p>代码写在 <code>${p.id}/main.cpp</code>。在「题单」里这道题那一行点「提交」，只评这一题。</p>`

/** The 题单 document's own words above its live list. */
export const WEEK3_INTRO = `<p>这周的 5 道题练输入输出、分支和循环，每题单独评测，以截止前最后一次提交为准。做完一题，上面那一行就变绿。</p>`

// ---- Shell Lab 大作业: three people, one tsh.c, judged on 8 traces ----

const TSH_C = `/*
 * tsh - A tiny shell program with job control
 *
 * ICS 第 7 组：林晓、周珂、陈思远
 */
#include "tsh.h"

/*
 * eval - Evaluate the command line that the user has just typed in.
 *   If the user has requested a built-in command (quit, jobs, bg or fg)
 *   then execute it immediately. Otherwise, fork a child process and
 *   run the job in the context of the child.
 */
void eval(char *cmdline) {
  char *argv[MAXARGS];
  int bg = parseline(cmdline, argv);
  if (argv[0] == NULL)
    return;
  if (builtin_cmd(argv))
    return;
  /* TODO: fork, execve, addjob, waitfg */
}

/*
 * builtin_cmd - If the user has typed a built-in command then execute
 *   it immediately and return 1, else return 0.
 */
int builtin_cmd(char **argv) {
  if (!strcmp(argv[0], "quit"))
    exit(0);
  /* TODO: jobs, bg, fg */
  return 0;
}

/* waitfg - Block until process pid is no longer the foreground process */
void waitfg(pid_t pid) {
  /* TODO */
}

/* sigchld_handler - reap all available zombie children */
void sigchld_handler(int sig) {
  /* TODO: waitpid(-1, &status, WNOHANG | WUNTRACED) */
}

/* sigint_handler - forward SIGINT to the foreground job */
void sigint_handler(int sig) {
  /* TODO */
}
`

const SHELL_README = `<h1>Shell Lab 大作业（3 人组队）</h1>
<p>计算机系统基础（ICS 2026） · 张老师发布。三个人一组，写一个带作业控制的简单 shell：前台、后台、<code>jobs</code> / <code>bg</code> / <code>fg</code>，以及 Ctrl-C、Ctrl-Z 的信号处理。</p>
<h3>分工建议</h3>
<ul><li><p>一个人写 <code>eval</code>：fork、execve、加进作业表</p></li><li><p>一个人写内置命令：<code>jobs</code>、<code>bg</code>、<code>fg</code></p></li><li><p>一个人写信号处理：<code>sigchld_handler</code>、<code>sigint_handler</code></p></li></ul>
<h3>评分</h3>
<p>老师评：提交 <code>tsh.c</code> 的一个版本和一段分工说明，张老师按 8 条 trace 和分工说明给分、写评语，全组一个分数。可以反复提交，以截止前最后一次为准。</p>
<h3>怎么一起写</h3>
<ul><li><p>领取时开好了一件事「做 Shell Lab」，三个人一起做：都在它的工作台里改同一份 <code>tsh.c</code>，谁在改哪一行都看得到。</p></li><li><p>做好了在那件事里「改好了」、采纳进正式版；提交时也可以直接交那件事里的当前版本。</p></li></ul>`

/** One trace of the Shell Lab driver: what it checks, and what in tsh.c makes it pass (the demo reads the code). */
const TRACES: { name: string; ok: (code: string) => boolean; note: string }[] = [
  { name: 'trace01 · 读到 EOF 正常退出', ok: () => true, note: '' },
  { name: 'trace02 · 内置命令 quit', ok: (c) => /"quit"/.test(c), note: '没有处理 quit' },
  {
    name: 'trace03 · 前台运行一个程序',
    ok: (c) => /\bfork\s*\(/.test(c) && /\bexecve\s*\(/.test(c),
    note: '命令没有运行：eval 里还没有 fork + execve',
  },
  {
    name: 'trace04 · 后台运行（&）',
    ok: (c) => /\baddjob\s*\(/.test(c),
    note: '后台作业没有进作业表：缺 addjob',
  },
  { name: 'trace05 · 内置命令 jobs', ok: (c) => /"jobs"/.test(c), note: 'jobs 没有输出' },
  { name: 'trace06 · Ctrl-C 转给前台作业', ok: (c) => /\bkill\s*\(\s*-/.test(c), note: 'SIGINT 没有转给前台进程组' },
  {
    name: 'trace07 · 回收结束的子进程',
    ok: (c) => /\bwaitpid\s*\(/.test(c),
    note: '有僵尸进程：sigchld_handler 没有 waitpid',
  },
  { name: 'trace08 · 内置命令 bg / fg', ok: (c) => /"bg"|"fg"/.test(c), note: 'bg / fg 不认识' },
]

function judgeShell(text: string): Verdict {
  const code = stripComments(text)
  const rows = TRACES.map((t, i) => {
    const ok = t.ok(code)
    return { name: t.name, ok, ms: 40 + i * 13, note: ok ? undefined : t.note }
  })
  const score = rows.filter((r) => r.ok).length
  const first = rows.find((r) => !r.ok)
  return {
    score,
    total: TRACES.length,
    rows,
    summary: first
      ? `${score}/${TRACES.length} · ${first.name.split(' · ')[0]} 没过`
      : `${score}/${TRACES.length} · 全部通过`,
  }
}

function runShell(text: string): { cmd: string; out: string } {
  const v = judgeShell(text)
  return {
    cmd: 'make && ./sdriver.pl -t trace01.txt … trace08.txt -s ./tsh',
    out: `${v.rows.map((r) => `${r.ok ? '✓' : '✗'} ${r.name}${r.ok ? '' : `  ${r.note}`}`).join('\n')}\n通过 ${v.score}/${v.total}`,
  }
}

// ---- what 领取 brings into the project ----

export interface ProjectFile {
  path: string
  kind: 'doc' | 'code' | 'list'
  bytes: number
  /** The 题面 / 题单 document, pinned at the top of 项目文件. */
  pinned?: boolean
  /** The graded unit whose submission takes a version of this file. */
  unit?: string
  html?: string
  starter?: string
}

export const PROJECT_FILES: Record<ClaimKey, ProjectFile[]> = {
  datalab: [
    { path: 'README.md', kind: 'doc', bytes: 1620, pinned: true, html: DATALAB_README },
    { path: 'bits.c', kind: 'code', bytes: BITS_C.length, unit: 'datalab', starter: BITS_C },
  ],
  week3: [
    { path: '题单.md', kind: 'list', bytes: 640, pinned: true },
    ...SET.flatMap((p) => [
      { path: `${p.id}/题面.md`, kind: 'doc' as const, bytes: 420, html: setStatement(p) },
      { path: `${p.id}/main.cpp`, kind: 'code' as const, bytes: p.starter.length, unit: p.id, starter: p.starter },
    ]),
  ],
  shelllab: [
    { path: 'README.md', kind: 'doc', bytes: 1480, pinned: true, html: SHELL_README },
    { path: 'tsh.c', kind: 'code', bytes: TSH_C.length, unit: 'shelllab', starter: TSH_C },
  ],
}

/** A graded unit: what 运行 prints, and what the judge says about a submitted version of its file. */
export interface Unit {
  id: string
  title: string
  path: string
  judge: (text: string) => Verdict
  run: (text: string, input?: RunInput) => RunOut
  /** A 题单 problem runs on a chosen input (样例 1 / 样例 2 / 自定义输入); a lab runs its own tests. */
  samples?: boolean
}

export const UNITS: Record<string, Unit> = {
  datalab: { id: 'datalab', title: 'Data Lab', path: 'bits.c', judge: judgeBits, run: runBits },
  shelllab: { id: 'shelllab', title: 'Shell Lab 大作业', path: 'tsh.c', judge: judgeShell, run: runShell },
  ...Object.fromEntries(
    SET.map((p) => [
      p.id,
      {
        id: p.id,
        title: `${p.id} ${p.title}`,
        path: `${p.id}/main.cpp`,
        judge: p.judge,
        run: (text: string, input?: RunInput) => runSet(p, text, input),
        samples: true,
      },
    ])
  ),
}

/** A submission's 提交项: the 题目's own, or for a 题单 the one problem's main.cpp. */
export function submitItems(t: Topic, unit: string): SubmitItem[] {
  if (t.category !== '题单') return t.items
  return [{ type: 'FILE', prompt: `${unit}/main.cpp`, path: `${unit}/main.cpp` }]
}

/** 张老师's review of a Shell Lab submission: she runs the 8 traces and reads the 分工说明 (the demo writes what she would). */
export function reviewShell(code: string, split: string): Omit<Review, 'at'> {
  const v = judgeShell(code)
  const failed = v.rows.filter((r) => !r.ok).map((r) => r.name.split(' · ')[0])
  const said = split.trim().length > 0
  const score = Math.round((v.score / v.total) * 80) + (said ? 20 : 0)
  const traces = failed.length
    ? `trace 过了 ${v.score}/${v.total}，${failed.slice(0, 3).join('、')}${failed.length > 3 ? ' 等' : ''}还没过，先看 eval 里的 fork 和作业表。`
    : `8 条 trace 都过了。`
  return {
    score,
    total: 100,
    by: 'zhanglaoshi',
    comment: `${traces}${said ? '分工说明写得清楚，三个人各管一块。' : '没有写分工说明，这一项不给分。'}截止前可以再交。`,
  }
}

/** The 一件事 领取 opens in a claimed project: the work itself, done by whoever claimed it (the whole team for a team 题目). */
export const WORK_TITLE: Record<ClaimKey, string> = {
  datalab: '做 Data Lab',
  week3: '做第 3 周题单',
  shelllab: '做 Shell Lab',
}

/** 芝士's first message in the project's # 综合: what the 题目 brought in, the 一件事 the work happens in, and
 *  the rule 芝士 works under. The 一件事's card sits under it. */
export function welcome(p: ClaimKey, due: string): ChatLine[] {
  const work = `「${WORK_TITLE[p]}」`
  const text =
    p === 'datalab'
      ? `这个项目是你从「Data Lab」领取的，在你自己的团队下。\n\n- 题面在 \`README.md\`，置顶在项目文件里\n- 领取时开好了一件事${work}，你来做：在它的工作台里写 \`bits.c\`、运行 btest\n- 做好了在这道题目上「提交」，评测机一次评全部，结果也记在题目上\n- 截止 ${due}，以截止前最后一次提交为准\n\n这门课设成「芝士只给提示」：在那件事的对话里问我思路，答案我不会写出来。`
      : p === 'week3'
        ? `这个项目是你从「第 3 周题单」领取的，在你自己的团队下。\n\n- 「题单」文档置顶在项目文件里，每道题做到哪了都在上面\n- 领取时开好了一件事${work}：5 道题的 \`main.cpp\` 都在它的工作台里，运行时挑样例或自己输入\n- 在题单里每题单独「提交」，截止 ${due}\n\n这门课设成「芝士只给提示」：卡住了在那件事的对话里问我思路，答案我不会写出来。`
        : `这个项目是「ICS 第 7 组」一起领取的「Shell Lab 大作业」：林晓、周珂、陈思远三个人共用。\n\n- 题面在 \`README.md\`，置顶在项目文件里，里面有分工建议\n- 领取时开好了一件事${work}，三个人一起做：都在它的工作台里改同一份 \`tsh.c\`，谁在改哪儿都看得到\n- 在这道题目上「提交」：交 \`tsh.c\` 的一个版本和分工说明，张老师批改，全组一个分数，截止 ${due}\n\n这门课设成「芝士只给提示」：思路可以在那件事的对话里问我，答案我不会写出来。`
  return [{ id: `${p}-w1`, who: 'cheese', at: new Date().toISOString(), text, thing: `w-${p}` }]
}

/** 芝士's answer in a course project: the course is set to 「只给提示」, so a hint, never code. */
export function hintFor(q: string): string {
  if (/答案|代码|直接|写出|给我|完整/.test(q))
    return '这门课的设置是「芝士只给提示，不给答案」，所以代码得你自己写。你说说卡在哪一步，我告诉你该往哪个方向想。'
  if (/bitXor|异或/.test(q))
    return '提示：异或是「x 和 y 恰好一个是 1」。先用「与」和「或」写出来，再用德摩根定律 `a | b = ~(~a & ~b)` 把「或」换掉。'
  if (/fork|execve|eval|tsh/.test(q))
    return '提示：子进程里 `execve`，父进程把它 `addjob` 进作业表；前台作业要等它结束，后台的不等。fork 之前先把 SIGCHLD 屏蔽掉，想想为什么。'
  if (/信号|SIGCHLD|SIGINT|waitpid|僵尸/.test(q))
    return '提示：`sigchld_handler` 里循环 `waitpid(-1, &status, WNOHANG | WUNTRACED)`，一次把能回收的都回收；Ctrl-C 要发给整个前台进程组，想想 `kill` 的第一个参数怎么写。'
  if (/logicalNeg/.test(q)) return '提示：想想哪个数和它的相反数（`~x + 1`）符号位都是 0。'
  if (/运算符|超|上限|dlc/.test(q))
    return '`dlc` 数的是你写的每一个运算符，括号不算，一个 `~` 算一个。先写对，再看能不能把重复的部分合并。'
  if (/P1001|A\+B|a \+ b/i.test(q))
    return '提示：`cin` 已经把两个数读进来了，剩下的是用 `cout` 把结果写出去，别忘了换行。'
  if (/错|不对|失败|过不了/.test(q))
    return '看这道题目提交记录里第一个 ✗ 的那一行：把那组输入自己算一遍，再和你的代码一步一步对。'
  return '可以说说你卡在哪个函数、试过什么吗？我只给提示，不写答案。'
}

// ---- the space's 公告 and 资料库 ----

export interface Announcement {
  id: string
  who: string
  at: string
  title: string
  body: string
  pinned?: boolean
}

export const ANNOUNCEMENTS: Announcement[] = [
  {
    id: 'a3',
    who: 'zhanglaoshi',
    at: dayAt(1, 10, 12),
    title: 'Shell Lab 大作业发布：3 人一组',
    body: '在「题目」里领取时选「和团队一起领」，选你们组的团队：全组共用一个项目，一个分数。还没组好队的，周五前在答疑时间找李助教。',
  },
  {
    id: 'a2',
    who: 'lizhujiao',
    at: dayAt(6, 9, 20),
    title: 'Data Lab：注意运算符限制',
    body: '只能用题面里列出的运算符，提交时 dlc 会检查，超了那个函数就是 0 分。可以反复提交，以截止前最后一次为准。',
  },
  {
    id: 'a1',
    who: 'zhanglaoshi',
    at: dayAt(9, 8, 40),
    title: '课程安排和评分',
    body: '五个 Lab 和大作业占 50%，期末 50%。答疑时间：周二、周四晚 7 点到 9 点，李助教。每道题领取后都是你（或你们组）自己的项目。',
    pinned: true,
  },
]

export interface LibraryFile {
  name: string
  kind: 'slides' | 'handout'
  bytes: number
  who: string
  at: string
}

export const LIBRARY: LibraryFile[] = [
  { name: '第 1 讲 课程介绍.pdf', kind: 'slides', bytes: 2_310_000, who: 'zhanglaoshi', at: dayAt(9, 8, 30) },
  { name: '第 2 讲 信息的表示和处理.pdf', kind: 'slides', bytes: 4_820_000, who: 'zhanglaoshi', at: dayAt(7, 9, 2) },
  { name: '第 3 讲 程序的机器级表示.pdf', kind: 'slides', bytes: 5_160_000, who: 'zhanglaoshi', at: dayAt(2, 8, 50) },
  { name: 'Data Lab 讲义.pdf', kind: 'handout', bytes: 640_000, who: 'lizhujiao', at: dayAt(6, 9, 10) },
  {
    name: 'Shell Lab 讲义：信号和作业控制.pdf',
    kind: 'handout',
    bytes: 910_000,
    who: 'lizhujiao',
    at: dayAt(1, 10, 20),
  },
  { name: 'gdb 速查表.pdf', kind: 'handout', bytes: 180_000, who: 'lizhujiao', at: dayAt(1, 10, 30) },
]
