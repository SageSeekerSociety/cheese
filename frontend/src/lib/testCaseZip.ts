// 「导入用例」：出题人把一包成对的用例文件（`1.in`/`1.out`、`1.in`/`1.ans`、
// `input1.txt`/`output1.txt`）打成 zip 传上来，在浏览器里拆开、配对，变成一组输入/输出
// 用例追加到表单上。不经过服务器：用例本来就随题目一起保存，这里只是省掉一个个粘贴。
//
// 限制和后端一致（`test_suite.MAX_INLINE_BYTES`、`MAX_CASES`）：单个输入或期望输出不超过
// 64 KB，一套最多 200 个用例。超了整包不导入并说是哪一个 —— 导一半最难查。大小先看中央
// 目录里记的数，超了的那一条不解压。
import type { TaskTestCase } from '@/types'

import { listZipSizes, readZipEntry } from './zipEntry'

/** 单个输入或期望输出最多多少字节（后端内联上限）。 */
export const MAX_INLINE_BYTES = 64 * 1024
/** 一套测试点最多几个用例。 */
export const MAX_CASES = 200
/** 导入的用例默认是隐藏的、10 分。 */
export const IMPORTED_POINTS = 10
/** 用例名称最长多少字（后端 `MAX_NAME_CHARS`）。 */
const MAX_NAME_CHARS = 64

/** 一对用例文件：名称、输入那一份、期望输出那一份（zip 里的全名）。 */
export interface CaseFilePair {
  name: string
  input: string
  output: string
}

/** 导入失败的原因：文案 key（`tasks.judge.import.<key>`）和它的参数。 */
export interface CaseImportError {
  key: 'notZip' | 'empty' | 'tooLarge' | 'tooMany' | 'notText'
  params?: Record<string, string | number>
}

export type CaseImportResult =
  | { ok: true; cases: TaskTestCase[]; unpaired: number }
  | { ok: false; error: CaseImportError }

type Side = 'in' | 'out'

/** 一份文件是哪一对的哪一边：`1.in`、`1.out`/`1.ans`、`input1.txt`/`output1.txt`。都不是给 null。 */
function sideOf(base: string): { stem: string; side: Side; rank: number } | null {
  const lower = base.toLowerCase()
  let m = /^(.+)\.in$/.exec(lower)
  if (m) return { stem: base.slice(0, m[1]!.length), side: 'in', rank: 0 }
  m = /^(.+)\.(out|ans)$/.exec(lower)
  if (m) return { stem: base.slice(0, m[1]!.length), side: 'out', rank: m[2] === 'out' ? 0 : 1 }
  m = /^input(.*)\.txt$/.exec(lower)
  if (m) return { stem: base.slice(5, 5 + m[1]!.length) || '1', side: 'in', rank: 0 }
  m = /^output(.*)\.txt$/.exec(lower)
  if (m) return { stem: base.slice(6, 6 + m[1]!.length) || '1', side: 'out', rank: 0 }
  return null
}

/** 目录、`__MACOSX/` 下的资源叉、点开头的隐藏文件不算用例文件。 */
const skipped = (path: string) =>
  path.endsWith('/') || path.startsWith('__MACOSX/') || path.split('/').some((part) => part.startsWith('.'))

/** 把一组文件名配成对，按名称的自然顺序（`2` 在 `10` 前面）。`1.out` 和 `1.ans` 都在时
 *  用 `.out`。配不上对的文件只计数。 */
export function pairCaseFiles(paths: string[]): { pairs: CaseFilePair[]; unpaired: number } {
  const groups = new Map<string, { dir: string; stem: string; in?: string; out?: string; outRank?: number }>()
  let unpaired = 0
  for (const path of paths) {
    if (skipped(path)) continue
    const cut = path.lastIndexOf('/')
    const dir = cut >= 0 ? path.slice(0, cut) : ''
    const found = sideOf(path.slice(cut + 1))
    if (!found) {
      unpaired += 1
      continue
    }
    const key = `${dir}\u0000${found.stem}`
    const group = groups.get(key) ?? { dir, stem: found.stem }
    if (found.side === 'in') {
      if (group.in) unpaired += 1
      group.in = path
    } else if (group.out == null || found.rank < (group.outRank ?? 9)) {
      if (group.out) unpaired += 1
      group.out = path
      group.outRank = found.rank
    } else {
      unpaired += 1
    }
    groups.set(key, group)
  }
  const complete = [...groups.values()].filter((g) => {
    if (g.in && g.out) return true
    unpaired += g.in || g.out ? 1 : 0
    return false
  })
  // 同一个名字出现在几个目录里：名称带上目录，免得几个「1」分不清。
  const stems = new Map<string, number>()
  for (const g of complete) stems.set(g.stem, (stems.get(g.stem) ?? 0) + 1)
  const pairs = complete
    .map((g) => ({
      name: ((stems.get(g.stem) ?? 0) > 1 && g.dir ? `${g.dir}/${g.stem}` : g.stem).slice(-MAX_NAME_CHARS),
      input: g.in!,
      output: g.out!,
    }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
  return { pairs, unpaired }
}

const decoder = () => new TextDecoder('utf-8', { fatal: true })

/** 拆开一个用例 zip，配好对，读成一组隐藏用例（每个 10 分）。`existing` 是表单上已经有
 *  几个用例：合起来超过 `MAX_CASES` 就不导入。 */
export async function casesFromZip(bytes: ArrayBuffer, existing = 0): Promise<CaseImportResult> {
  const listed = listZipSizes(bytes)
  if (!listed) return { ok: false, error: { key: 'notZip' } }
  const { pairs, unpaired } = pairCaseFiles(listed.map((entry) => entry.name))
  if (!pairs.length) return { ok: false, error: { key: 'empty' } }
  if (existing + pairs.length > MAX_CASES) {
    return { ok: false, error: { key: 'tooMany', params: { n: pairs.length, max: MAX_CASES, existing } } }
  }
  const sizes = new Map(listed.map((entry) => [entry.name, entry.size]))
  const kb = MAX_INLINE_BYTES / 1024
  for (const pair of pairs) {
    for (const path of [pair.input, pair.output]) {
      if ((sizes.get(path) ?? 0) > MAX_INLINE_BYTES) {
        return { ok: false, error: { key: 'tooLarge', params: { file: path, kb } } }
      }
    }
  }
  const cases: TaskTestCase[] = []
  for (const pair of pairs) {
    const texts: string[] = []
    for (const path of [pair.input, pair.output]) {
      const data = await readZipEntry(bytes, path)
      if (!data) return { ok: false, error: { key: 'notZip' } }
      // 中央目录里的数可以是假的：解出来再量一次。
      if (data.length > MAX_INLINE_BYTES) return { ok: false, error: { key: 'tooLarge', params: { file: path, kb } } }
      try {
        texts.push(decoder().decode(data))
      } catch {
        return { ok: false, error: { key: 'notText', params: { file: path } } }
      }
    }
    cases.push({
      name: pair.name,
      points: IMPORTED_POINTS,
      visibility: 'HIDDEN',
      input: texts[0]!,
      expected: texts[1]!,
    })
  }
  return { ok: true, cases, unpaired }
}

/** 浏览器里一份文件读成测试文件：能按 UTF-8 读的给 `content`，读不了的给 `base64`
 *  （后端两种都收，`test_files._bytes_of`）。 */
export async function readTestFile(
  file: File,
  path = file.name
): Promise<{
  path: string
  content?: string
  base64?: string
  bytes: number
}> {
  const data = new Uint8Array(await file.arrayBuffer())
  try {
    return { path, content: decoder().decode(data), bytes: data.length }
  } catch {
    let binary = ''
    // 一次拼一段：`String.fromCharCode(...data)` 遇到几百 KB 的文件会爆栈。
    for (let at = 0; at < data.length; at += 0x8000) {
      binary += String.fromCharCode(...data.subarray(at, at + 0x8000))
    }
    return { path, base64: btoa(binary), bytes: data.length }
  }
}
