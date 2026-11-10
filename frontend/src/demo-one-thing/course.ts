// The course space 「计算机系统基础（ICS 2026）」: the 题目 the teacher published
// in it, what each 题目 brings into the project a student gets on 领取, and the
// judges. A 题目 is not a kind of screen: once claimed it is an ordinary
// project (channels, 项目文件, 设置) that holds the 题面 as a document, the
// starter code, the rules in its settings and a judge on the files of a judged
// unit. The space itself holds only the 题目 list and the course-wide channels.
//
// This is the student's side only. The teacher's views (the class's
// submissions, the grade sheet, writing a 题目 and its tests) exist in the
// product and are not part of this demo.
import type { ChatLine } from './state'

import { type ClaimKey, dayAt, minsAgo, NOW } from './data'

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

/** One 提交评测: the verdict, pinned to the exact version of 正式版 it judged. */
export interface Submission extends Verdict {
  id: string
  /** The judged unit: 'datalab', or one problem of the 题单 ('P1001'). */
  unit: string
  /** 第 n 次提交 of this unit. */
  n: number
  at: string
  /** The version's short id and its text, so anyone can see which code was graded. */
  version: string
  code: string
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

export interface Topic {
  id: string
  title: string
  kind: 'lab' | 'set'
  about: string
  due: string
  published: string
  /** The project 领取 creates; a 题目 without one is shown but not claimable in the demo. */
  project?: ClaimKey
}

export const TOPICS: Topic[] = [
  {
    id: 'datalab',
    title: 'Data Lab',
    kind: 'lab',
    about: '在 bits.c 里用位运算写 13 个函数，一起评分',
    due: daysAhead(3, 23, 59),
    published: dayAt(6, 9, 0),
    project: 'datalab',
  },
  {
    id: 'week3',
    title: '第 3 周题单',
    kind: 'set',
    about: '5 道小题：输入输出、分支和循环，每题单独评测',
    due: daysAhead(5, 23, 59),
    published: dayAt(2, 8, 30),
    project: 'week3',
  },
  {
    id: 'cachelab',
    title: 'Cache Lab',
    kind: 'lab',
    about: '写一个缓存模拟器，再写一个对缓存友好的矩阵转置',
    due: daysAhead(12, 23, 59),
    published: dayAt(1, 10, 5),
  },
  {
    id: 'bomblab',
    title: 'Bomb Lab',
    kind: 'lab',
    about: '用 gdb 读汇编，拆掉六关炸弹',
    due: daysAhead(19, 23, 59),
    published: dayAt(1, 10, 6),
  },
]

export const topicById = (id: string) => TOPICS.find((t) => t.id === id)
export const topicOf = (p: ClaimKey) => TOPICS.find((t) => t.project === p)!

/** 「3 天后截止」 for a 题目 due within a week, else nothing. */
export function dueChip(iso: string): string | null {
  const days = Math.floor((Date.parse(iso) - NOW) / 86_400_000)
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
<ul><li><p>打开 <code>bits.c</code> 直接改，保存就生效，历史里每次保存都能撤回。</p></li><li><p>「运行」在你自己的环境里跑 <code>./btest</code>，只有你看得到。</p></li><li><p>「提交评测」评的是你提交那一刻的版本，评测记录里每次都能看到当时的代码。</p></li></ul>`

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
  starter: string
  /** The demo's judge for this problem: the verdict on a version of main.cpp. */
  judge: (code: string) => Verdict
  /** What main.cpp prints on the sample input. */
  run: (code: string) => string
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
    run: (code) => (printsSum(code) ? '50' : printsAnything(code) ? '(输出和样例不一样)' : '(没有输出)'),
  },
  {
    id: 'P1046',
    title: '陶陶摘苹果',
    statement: '树上 10 个苹果的高度，和陶陶伸手能摸到的高度。陶陶站上 30 厘米的板凳也能摘，问能摘到几个。',
    sampleIn: '100 200 150 140 129 134 167 198 200 111\n110',
    sampleOut: '5',
    starter: `${CPP_HEAD}  int h[10], reach;\n  for (int i = 0; i < 10; i++) cin >> h[i];\n  cin >> reach;\n  // 数一数能摘到几个\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 7，你的输出是 5（板凳的 30 厘米算进去了吗）'),
    run: (code) => (printsAnything(code) ? '5' : '(没有输出)'),
  },
  {
    id: 'P1085',
    title: '不高兴的津津',
    statement: '一周七天，每天上学和补习的小时数。超过 8 小时津津就不高兴，输出最不高兴的那天，都高兴输出 0。',
    sampleIn: '5 3\n6 2\n7 2\n5 3\n5 4\n0 4\n0 6',
    sampleOut: '3',
    starter: `${CPP_HEAD}  // 读 7 行，每行两个数\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 2，你的输出是 5（一样不高兴时输出最早的那天）'),
    run: (code) => (printsAnything(code) ? '3' : '(没有输出)'),
  },
  {
    id: 'P1089',
    title: '津津的储蓄计划',
    statement: '每月初拿到 300 元，预算花完后把整百的钱存给妈妈，年底多加 20%。某月不够花就输出 -那个月。',
    sampleIn: '290\n230\n280\n200\n300\n170\n340\n50\n90\n80\n200\n60',
    sampleOut: '-7',
    starter: `${CPP_HEAD}  int budget[12];\n  for (int i = 0; i < 12; i++) cin >> budget[i];\n  // 按月模拟\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 1580，你的输出是 1500（年底的 20% 加了吗）'),
    run: (code) => (printsAnything(code) ? '-7' : '(没有输出)'),
  },
  {
    id: 'P5703',
    title: '苹果采购',
    statement: '每个苹果 a 元，要买 b 个，输出一共多少元。',
    sampleIn: '5 3',
    sampleOut: '15',
    starter: `${CPP_HEAD}  long long a, b;\n  cin >> a >> b;\n  return 0;\n}\n`,
    judge: scriptedJudge('期望 1000000000000，你的输出溢出了'),
    run: (code) => (printsAnything(code) ? '15' : '(没有输出)'),
  },
]

export const setProblem = (id: string) => SET.find((p) => p.id === id)

const setStatement = (p: SetProblem) => `<h2>${p.id} ${p.title}</h2>
<p>${p.statement}</p>
<h3>样例输入</h3>
<pre><code>${p.sampleIn}</code></pre>
<h3>样例输出</h3>
<pre><code>${p.sampleOut}</code></pre>
<p>代码写在 <code>${p.id}/main.cpp</code>，「提交评测」只评这一题。</p>`

/** The 题单 document's own words above its live list. */
export const WEEK3_INTRO = `<p>这周的 5 道题练输入输出、分支和循环，每题单独评测，以截止前最后一次提交为准。做完一题，上面那一行就变绿。</p>`

// ---- what 领取 brings into the project ----

export interface ProjectFile {
  path: string
  kind: 'doc' | 'code' | 'list'
  bytes: number
  /** The 题面 / 题单 document, pinned at the top of 项目文件. */
  pinned?: boolean
  /** A code file of a judged unit: 提交评测 shows on it. */
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
}

/** A judged unit: what 运行 prints and what 提交评测 says, on a version of its file. */
export interface Unit {
  id: string
  title: string
  path: string
  judge: (text: string) => Verdict
  run: (text: string) => { cmd: string; out: string }
}

export const UNITS: Record<string, Unit> = {
  datalab: { id: 'datalab', title: 'Data Lab', path: 'bits.c', judge: judgeBits, run: runBits },
  ...Object.fromEntries(
    SET.map((p) => [
      p.id,
      {
        id: p.id,
        title: `${p.id} ${p.title}`,
        path: `${p.id}/main.cpp`,
        judge: p.judge,
        run: (text: string) => ({
          cmd: `g++ -O2 main.cpp -o main && ./main < 样例.in`,
          out: `${p.run(text)}\n样例输出：${p.sampleOut}`,
        }),
      },
    ])
  ),
}

/** 芝士's first message in the project's # 综合: what the 题目 brought in, and the rule it works under. */
export function welcome(p: ClaimKey, due: string): ChatLine[] {
  const lab = p === 'datalab'
  return [
    {
      id: `${p}-w1`,
      who: 'cheese',
      at: new Date().toISOString(),
      text: lab
        ? `这个项目是你从「Data Lab」领取的。\n\n- 题面在 \`README.md\`，置顶在项目文件里\n- 代码在 \`bits.c\`：13 个函数，改完保存就生效；「提交评测」一次评全部\n- 截止 ${due}，以截止前最后一次提交为准\n\n这门课设成「芝士只给提示」：思路可以问我，答案我不会写出来。`
        : `这个项目是你从「第 3 周题单」领取的。\n\n- 「题单」文档置顶在项目文件里，每道题做到哪了都在上面\n- 每道题一个文件夹：\`题面.md\` 和 \`main.cpp\`，每题单独提交评测\n- 截止 ${due}\n\n这门课设成「芝士只给提示」：卡住了可以问我思路，答案我不会写出来。`,
    },
  ]
}

/** 芝士's answer in a course project: the course is set to 「只给提示」, so a hint, never code. */
export function hintFor(q: string): string {
  if (/答案|代码|直接|写出|给我|完整/.test(q))
    return '这门课的设置是「芝士只给提示，不给答案」，所以代码得你自己写。你说说卡在哪一步，我告诉你该往哪个方向想。'
  if (/bitXor|异或/.test(q))
    return '提示：异或是「x 和 y 恰好一个是 1」。先用「与」和「或」写出来，再用德摩根定律 `a | b = ~(~a & ~b)` 把「或」换掉。'
  if (/logicalNeg/.test(q)) return '提示：想想哪个数和它的相反数（`~x + 1`）符号位都是 0。'
  if (/运算符|超|上限|dlc/.test(q))
    return '`dlc` 数的是你写的每一个运算符，括号不算，一个 `~` 算一个。先写对，再看能不能把重复的部分合并。'
  if (/P1001|A\+B|a \+ b/i.test(q))
    return '提示：`cin` 已经把两个数读进来了，剩下的是用 `cout` 把结果写出去，别忘了换行。'
  if (/错|不对|失败|过不了/.test(q))
    return '看评测记录里第一个 ✗ 的那一行：把那组输入自己算一遍，再和你的代码一步一步对。'
  return '可以说说你卡在哪个函数、试过什么吗？我只给提示，不写答案。'
}

// ---- the course's channels: the space's own discussion, not any student's project ----

export const COURSE_CHAT: Record<string, ChatLine[]> = {
  'k-general': [
    {
      id: 'n1',
      who: 'zhanglaoshi',
      at: dayAt(6, 9, 0),
      text: 'Data Lab 发布了，在课程主页里「领取」，每人会有一个自己的项目，题面和 bits.c 都在里面。可以反复提交，以截止前最后一次为准。',
    },
    {
      id: 'n2',
      who: 'lizhujiao',
      at: dayAt(6, 9, 20),
      text: '提醒：Data Lab 只能用题面里列出的运算符，提交时 `dlc` 会检查，超了那个函数就是 0 分。',
    },
    { id: 'n3', who: 'zhanglaoshi', at: dayAt(2, 8, 31), text: '第 3 周题单发了，5 道小题，每题单独评测。' },
    { id: 'n4', who: 'wuhao', at: minsAgo(55), text: 'Bomb Lab 的 gdb 是在自己电脑上跑吗？' },
    {
      id: 'n5',
      who: 'lizhujiao',
      at: minsAgo(50),
      text: '领取之后你的项目里有自己的终端，就在那儿跑。炸弹爆了服务器会记，先在 `explode_bomb` 上下断点。',
    },
  ],
  'k-qa': [
    { id: 'q1', who: 'zhouzihan', at: dayAt(1, 21, 3), text: 'bitXor 只能用 ~ 和 &，是不是要用德摩根？' },
    {
      id: 'q2',
      who: 'lizhujiao',
      at: dayAt(1, 21, 30),
      text: '方向对。先把异或写成「或」和「与」的组合，再把「或」换掉。具体式子自己推，别在这里贴代码 🙂',
    },
    { id: 'q3', who: 'wuhao', at: minsAgo(80), text: 'logicalNeg 不能用 !，有人有思路吗' },
    { id: 'q4', who: 'zhouzihan', at: minsAgo(75), text: '想想 0 和它的相反数有什么特别的' },
    {
      id: 'q5',
      who: 'lizhujiao',
      at: minsAgo(30),
      text: 'P1046 记得把 30 厘米的板凳算进去，好几个人在这儿错了。',
    },
  ],
}
