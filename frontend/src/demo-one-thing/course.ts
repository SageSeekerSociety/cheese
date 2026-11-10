// The course 「计算机系统基础（ICS 2026）」: its channels' messages, its 题目 and
// the judge. A course is the same kind of space as a project with two course
// switches set (设置 › 课程): code is one copy per person, edited directly, and
// a 题目 is judged on submit. 芝士 inside a 题目 is a tutor that only hints.
//
// This is the student's side only. The teacher's views (the class's
// submissions, the grade sheet, writing a 题目 and its tests) exist in the
// product and are not part of this demo.
import type { ChatLine } from './state'

import { dayAt, minsAgo, NOW } from './data'

export interface JudgeRow {
  name: string
  ok: boolean
  ms: number
  note?: string
}

export interface Submission {
  id: string
  at: string
  score: number
  total: number
  rows: JudgeRow[]
  /** One line under the score: 运算符 count, or what failed. */
  summary: string
}

/** A C function the demo judges for real: its expression is parsed and run on the tests. */
interface FnSpec {
  name: string
  args: string[]
  legal: string[]
  maxOps: number
  ref: (...a: number[]) => number
  tests: number[][]
  samples: number[][]
}

export interface Problem {
  id: string
  title: string
  file: string
  /** gdb: no code to write here, the answers come from the person's own terminal. */
  kind: 'code' | 'gdb'
  due: string
  starter: string
  statement: string
  fn?: FnSpec
  /** For a program the demo cannot run (a cache simulator): what the judge says, and what 运行样例 prints. */
  scripted?: { result: Omit<Submission, 'id' | 'at'>; sample: string }
  /** What 芝士 says when asked about this 题目 (a hint, never the answer). */
  hint: string
}

const daysAhead = (d: number, h: number, m: number) => dayAt(-d, h, m)
const int = (n: number) => n | 0

const BITXOR_STARTER = `/*
 * bitXor - x^y using only ~ and &
 *   Example: bitXor(4, 5) = 1
 *   Legal ops: ~ &
 *   Max ops: 14
 *   Rating: 1
 */
int bitXor(int x, int y) {
  return 2;
}
`

const LOGICALNEG_MINE = `/*
 * logicalNeg - implement the ! operator, using all of
 *              the legal operators except !
 *   Examples: logicalNeg(3) = 0, logicalNeg(0) = 1
 *   Legal ops: ~ & ^ | + << >>
 *   Max ops: 12
 *   Rating: 4
 */
int logicalNeg(int x) {
  return ((x | (~x + 1)) >> 31) + 1;
}
`

const CSIM_STARTER = `#include "cachelab.h"
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct {
  int valid;
  unsigned long tag;
  int lru;
} line_t;

int hits = 0, misses = 0, evictions = 0;

void access(line_t *set, int E, unsigned long tag, int now) {
  for (int i = 0; i < E; i++)
    if (set[i].valid && set[i].tag == tag) {
      hits++;
      return;
    }
  misses++;
  /* TODO: 找空行；没有空行就按 LRU 换掉一行 */
}

int main(int argc, char *argv[]) {
  /* 解析 -s -E -b -t，逐行读 trace，调用 access */
  printSummary(hits, misses, evictions);
  return 0;
}
`

const TRANS_STARTER = `#include "cachelab.h"

char transpose_submit_desc[] = "Transpose submission";
void transpose_submit(int M, int N, int A[N][M], int B[M][N]) {
  for (int i = 0; i < N; i++)
    for (int j = 0; j < M; j++)
      B[j][i] = A[i][j];
}
`

export const PROBLEMS: Problem[] = [
  {
    id: 'p-bitxor',
    title: 'Data Lab：bitXor',
    file: 'bits.c',
    kind: 'code',
    due: daysAhead(3, 23, 59),
    starter: BITXOR_STARTER,
    statement: `只用 \`~\` 和 \`&\` 实现 \`x ^ y\`。

**规则**

- 合法运算符：\`~ &\`
- 最多 14 个运算符
- 常量只能是 0 到 255（\`0xFF\`）
- 不能用 if、循环、函数调用和宏，不能用 \`int\` 以外的类型

**示例**

| 调用 | 结果 |
| --- | --- |
| \`bitXor(4, 5)\` | 1 |
| \`bitXor(0, 0)\` | 0 |

**评测**：4 组测试全对且运算符不超限得 1 分，\`dlc\` 不通过得 0 分。`,
    fn: {
      name: 'bitXor',
      args: ['x', 'y'],
      legal: ['~', '&'],
      maxOps: 14,
      ref: (x, y) => int(x ^ y),
      samples: [
        [4, 5],
        [0, 0],
      ],
      tests: [
        [4, 5],
        [0, 0],
        [-1, 0x7fffffff],
        [0x55555555, int(0xaaaaaaaa)],
      ],
    },
    hint: '提示：异或是「x 和 y 恰好一个是 1」。先用「与」和「或」写出来，再用德摩根定律 `a | b = ~(~a & ~b)` 把「或」换掉。',
  },
  {
    id: 'p-logicalneg',
    title: 'Data Lab：logicalNeg',
    file: 'bits.c',
    kind: 'code',
    due: daysAhead(3, 23, 59),
    starter: LOGICALNEG_MINE,
    statement: `不用 \`!\`，实现 \`!x\`。

**规则**

- 合法运算符：\`~ & ^ | + << >>\`
- 最多 12 个运算符
- 常量只能是 0 到 255

**示例**

| 调用 | 结果 |
| --- | --- |
| \`logicalNeg(3)\` | 0 |
| \`logicalNeg(0)\` | 1 |

**评测**：4 组测试全对且运算符不超限得 4 分。`,
    fn: {
      name: 'logicalNeg',
      args: ['x'],
      legal: ['~', '&', '^', '|', '+', '<<', '>>'],
      maxOps: 12,
      ref: (x) => (x === 0 ? 1 : 0),
      samples: [[3], [0]],
      tests: [[0], [3], [-2147483648], [2147483647]],
    },
    hint: '提示：想想哪个数和它的相反数（`~x + 1`）符号位都是 0。',
  },
  {
    id: 'p-bomb1',
    title: 'Bomb Lab：phase_1',
    file: 'answers.txt',
    kind: 'gdb',
    due: daysAhead(10, 23, 59),
    starter: '',
    statement: `拆掉炸弹的第一关：找出 \`phase_1\` 要的那一行字符串。

**怎么做**

- 这一题不写代码：在你自己的终端里用 \`gdb ./bomb\` 读汇编、下断点
- 把每一关的答案按行写进 \`answers.txt\`，\`./bomb answers.txt\` 自己验证
- 炸弹每爆一次扣 0.5 分，服务器会记下来

**评测**：拆掉得 10 分。`,
    hint: '提示：先在 `explode_bomb` 上下断点，再看 `phase_1` 把你的输入和哪个地址上的字符串比较。',
  },
  {
    id: 'p-csim',
    title: 'Cache Lab：Part A 缓存模拟器',
    file: 'csim.c',
    kind: 'code',
    due: daysAhead(1, 23, 59),
    starter: CSIM_STARTER,
    statement: `写一个缓存模拟器 \`csim\`：读 valgrind 的内存访问 trace，按给定的 \`-s -E -b\` 模拟一个 LRU 缓存，输出命中、不命中和替换的次数。

**规则**

- 和参考实现 \`csim-ref\` 在 8 个 trace 上的三个数都一样才得分
- 替换策略是 LRU
- 用 \`printSummary(hits, misses, evictions)\` 输出

**评测**：\`./test-csim\`，满分 27 分。`,
    scripted: {
      result: {
        score: 15,
        total: 27,
        rows: [
          { name: 'yi2.trace (s=1, E=1, b=1)', ok: true, ms: 3 },
          { name: 'yi.trace (s=4, E=2, b=4)', ok: true, ms: 4 },
          { name: 'dave.trace (s=2, E=1, b=4)', ok: true, ms: 3 },
          { name: 'trans.trace (s=2, E=1, b=3)', ok: true, ms: 6 },
          { name: 'trans.trace (s=2, E=2, b=3)', ok: false, ms: 6, note: 'evictions 是 0，参考是 191' },
          { name: 'trans.trace (s=2, E=4, b=3)', ok: false, ms: 6, note: 'evictions 是 0，参考是 172' },
          { name: 'trans.trace (s=5, E=1, b=5)', ok: true, ms: 7 },
          { name: 'long.trace (s=5, E=1, b=5)', ok: false, ms: 41, note: 'misses 多了 2 次' },
        ],
        summary: '15/27 · E > 1 时没有按 LRU 替换',
      },
      sample:
        '$ ./csim -s 1 -E 1 -b 1 -t traces/yi2.trace\nhits:9 misses:8 evictions:0\n参考：hits:9 misses:8 evictions:6',
    },
    hint: '提示：每次访问都把这一行的 `lru` 记成当前时刻；要换的时候，找 `lru` 最小的那一行。',
  },
  {
    id: 'p-trans',
    title: 'Cache Lab：Part B 矩阵转置',
    file: 'trans.c',
    kind: 'code',
    due: daysAhead(5, 23, 59),
    starter: TRANS_STARTER,
    statement: `写一个对缓存友好的矩阵转置 \`transpose_submit\`，让 miss 次数尽量少。

**规则**

- 缓存是 s=5, E=1, b=5（1 KB，直接映射）
- 最多用 12 个 \`int\` 局部变量，不能改 A，不能开数组

**评测**：32×32 要少于 300 次 miss，64×64 少于 1300，61×67 少于 2000，满分 26 分。`,
    scripted: {
      result: {
        score: 0,
        total: 26,
        rows: [
          { name: '32×32', ok: false, ms: 120, note: '1183 次 miss，要少于 300' },
          { name: '64×64', ok: false, ms: 410, note: '4723 次 miss，要少于 1300' },
          { name: '61×67', ok: false, ms: 390, note: '4423 次 miss，要少于 2000' },
        ],
        summary: '0/26 · 逐行转置，三个尺寸都超了',
      },
      sample: '$ ./test-trans -M 32 -N 32\nfunc 0 (Transpose submission): hits:869, misses:1183, evictions:1151',
    },
    hint: '提示：按 8×8 的块来转置，块里先把一行读进局部变量再写出去，对角线上的块要单独想。',
  },
]

export const problemById = (id: string | null) => PROBLEMS.find((p) => p.id === id) ?? null

/** Earlier submissions, newest first: logicalNeg passed yesterday, Part A tried twice. */
export function seedSubmissions(): Record<string, Submission[]> {
  const csim = PROBLEMS.find((p) => p.id === 'p-csim')!.scripted!.result
  return {
    'p-logicalneg': [
      {
        id: 's-ln1',
        at: dayAt(1, 22, 14),
        score: 4,
        total: 4,
        rows: [
          { name: 'logicalNeg(0) = 1', ok: true, ms: 0.3 },
          { name: 'logicalNeg(3) = 0', ok: true, ms: 0.2 },
          { name: 'logicalNeg(-2147483648) = 0', ok: true, ms: 0.2 },
          { name: 'logicalNeg(2147483647) = 0', ok: true, ms: 0.2 },
        ],
        summary: '4/4 · 用了 5 个运算符，上限 12',
      },
    ],
    'p-csim': [
      { ...csim, id: 's-cs2', at: minsAgo(140), rows: csim.rows.map((r) => ({ ...r })) },
      {
        id: 's-cs1',
        at: dayAt(1, 20, 3),
        score: 9,
        total: 27,
        rows: csim.rows.map((r, i) => ({ ...r, ok: i < 3, note: i < 3 ? undefined : 'misses 和参考不一样' })),
        summary: '9/27 · 没有处理 E > 1 的组相联',
      },
    ],
  }
}

/** Where a 题目 stands for me: 已通过, 尝试中 (n 次) or 未开始. The latest submission
 *  counts (the course's rule), so a later failing one takes 已通过 back. */
export function standing(subs: Submission[] | undefined): 'passed' | 'trying' | 'new' {
  const last = subs?.[0]
  if (!last) return 'new'
  return last.score === last.total ? 'passed' : 'trying'
}

/** 「明天截止」 for a 题目 due within three days, else nothing. */
export function dueChip(iso: string): string | null {
  const days = Math.floor((Date.parse(iso) - NOW) / 86_400_000)
  if (days > 3 || days < 0) return null
  return days === 0 ? '今天截止' : days === 1 ? '明天截止' : `${days} 天后截止`
}

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

function run(e: Ast, env: Record<string, number>): number {
  switch (e.k) {
    case 'num':
      return e.v
    case 'var':
      if (!(e.name in env)) throw new Error(`'${e.name}' 没有声明`)
      return env[e.name]!
    case 'un': {
      const a = run(e.a, env)
      return e.op === '~' ? ~a : e.op === '!' ? (a === 0 ? 1 : 0) : int(-a)
    }
    default: {
      const a = run(e.a, env)
      const b = run(e.b, env)
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

/** Find the function in the file and compile its body; a C-compiler-like message on failure. */
function compile(text: string, file: string, fn: FnSpec): Compiled {
  const code = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' ')).replace(/\/\/.*$/gm, '')
  const head = new RegExp(`int\\s+${fn.name}\\s*\\(([^)]*)\\)\\s*\\{`).exec(code)
  if (!head) throw new Error(`${file}: 找不到函数 ${fn.name}`)
  const line = code.slice(0, head.index).split('\n').length
  const close = code.indexOf('}', head.index)
  if (close < 0) throw new Error(`${file}:${line}: 函数 ${fn.name} 少了 '}'`)
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
      throw new Error(`${file}:${line}: ${fn.name}: ${(e as Error).message}`)
    }
  }
  if (steps.at(-1)?.name !== null) throw new Error(`${file}:${line}: ${fn.name}: 函数最后要 return 一个值`)
  return {
    line,
    ops: steps.flatMap((s) => s.p.ops),
    consts: steps.flatMap((s) => s.p.consts),
    call: (args) => {
      const env: Record<string, number> = Object.fromEntries(fn.args.map((a, i) => [a, int(args[i]!)]))
      for (const s of steps) {
        const v = run(s.p.ast, env)
        if (s.name === null) return v
        env[s.name] = v
      }
      return 0
    },
  }
}

const callText = (fn: FnSpec, args: number[]) => `${fn.name}(${args.join(', ')})`
/** A test's run time: small and steady, as btest prints it. */
const msOf = (args: number[]) => (2 + (Math.abs(args.reduce((a, b) => a + b, 0)) % 4)) / 10

/** 运行样例: the samples from the statement, run on the code as it now reads. */
export function runSamples(p: Problem, text: string): string {
  if (!p.fn) return p.scripted?.sample ?? ''
  const fn = p.fn
  const out = [`$ ./btest -f ${fn.name}`]
  try {
    const c = compile(text, p.file, fn)
    for (const args of fn.samples) {
      const want = fn.ref(...args)
      const got = c.call(args)
      out.push(`${got === want ? '✓' : '✗'} ${callText(fn, args)} = ${got}${got === want ? '' : `，应该是 ${want}`}`)
    }
  } catch (e) {
    out.push(`编译错误：${(e as Error).message}`)
  }
  return out.join('\n')
}

/** 提交评测: every test, then dlc's operator and constant checks. */
export function judge(p: Problem, text: string): Omit<Submission, 'id' | 'at'> {
  if (!p.fn) return structuredClone(p.scripted!.result)
  const fn = p.fn
  const total = fn.tests.length
  let c: Compiled
  try {
    c = compile(text, p.file, fn)
  } catch (e) {
    return { score: 0, total, rows: [], summary: `0/${total} · 编译错误：${(e as Error).message}` }
  }
  const rows: JudgeRow[] = fn.tests.map((args) => {
    const want = fn.ref(...args)
    let got: number | string
    try {
      got = c.call(args)
    } catch (e) {
      got = (e as Error).message
    }
    return {
      name: `${callText(fn, args)} = ${want}`,
      ok: got === want,
      ms: msOf(args),
      note: got === want ? undefined : `你的结果是 ${got}`,
    }
  })
  const illegal = c.ops.find((o) => !fn.legal.includes(o))
  const bigConst = c.consts.find((k) => Number(k) > 255)
  const dlc = illegal
    ? `dlc：${p.file}:${c.line}:${fn.name}: Illegal operator (${illegal})`
    : bigConst
      ? `dlc：${p.file}:${c.line}:${fn.name}: Illegal constant (${bigConst})，只能用 0 到 0xFF`
      : c.ops.length > fn.maxOps
        ? `dlc：${fn.name} 用了 ${c.ops.length} 个运算符，上限 ${fn.maxOps}`
        : null
  const passed = rows.filter((r) => r.ok).length
  const score = dlc ? 0 : passed
  const opsLine = `用了 ${c.ops.length} 个运算符，上限 ${fn.maxOps}`
  return { score, total, rows, summary: `${score}/${total} · ${dlc ?? opsLine}` }
}

// ---- the course's channels ----

export const COURSE_CHAT: Record<string, ChatLine[]> = {
  'k-general': [
    {
      id: 'n1',
      who: 'zhanglaoshi',
      at: dayAt(3, 9, 0),
      text: 'Data Lab 发布了，在左边「题目」里，截止时间写在每道题的题面里。每道题都可以反复提交，以截止前最后一次为准。',
    },
    {
      id: 'n2',
      who: 'lizhujiao',
      at: dayAt(3, 9, 20),
      text: '提醒：Data Lab 只能用题面里列出的运算符，提交时 `dlc` 会检查，超了这道题就是 0 分。',
    },
    { id: 'n3', who: 'zhanglaoshi', at: dayAt(1, 10, 5), text: 'Cache Lab 也发了，Part A 先截止，Part B 晚几天。' },
    { id: 'n4', who: 'wuhao', at: minsAgo(55), text: 'Bomb Lab 的 gdb 是在自己电脑上跑吗？' },
    {
      id: 'n5',
      who: 'lizhujiao',
      at: minsAgo(50),
      text: '在课程服务器上，每人一台。炸弹爆了服务器会记，先在 `explode_bomb` 上下断点。',
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
      text: 'Cache Lab Part A 的 trace 文件在 `traces/` 下，`./test-csim` 会把你的结果和参考实现对比。',
    },
  ],
}

/** 问芝士 inside bitXor: the exchange already there when the page opens. */
export const TUTOR_SEED: Record<string, ChatLine[]> = {
  'p-bitxor': [
    { id: 'k1', who: 'linxiao', at: minsAgo(22), text: '异或怎么只用 ~ 和 & 表示？没有 | 好难' },
    {
      id: 'k2',
      who: 'cheese',
      at: minsAgo(21),
      text: '先不急着凑运算符。异或是「x 和 y 恰好一个是 1」，可以拆成两种情况再「或」起来。\n\n「或」没有，但德摩根定律说 `a | b` 等于 `~(~a & ~b)`。把这两步接起来试试，写完先点「运行样例」。',
    },
  ],
}

/** 芝士's answer to a question in a 题目: course setting 「只给提示」, so a hint, never code. */
export function tutorAnswer(p: Problem, q: string): string {
  if (/答案|代码|直接|写出|给我|完整/.test(q))
    return `这门课的设置是「芝士只给提示，不给答案」，所以式子得你自己写。\n\n${p.hint}`
  if (/运算符|超|上限|几个|dlc/.test(q))
    return '`dlc` 数的是你写的每一个运算符，括号不算，一个 `~` 算一个。超了就是 0 分，所以先写对，再看能不能把重复的部分合并。'
  if (/错|不对|失败|过不了/.test(q))
    return '看「评测记录」里第一个 ✗ 的那组：把输入换成二进制，一位一位算你的式子，看是哪一位和期望不一样。'
  return p.hint
}
