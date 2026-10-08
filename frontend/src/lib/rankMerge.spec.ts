/**
 * 两串各自翻页的结果并成一张表：不论每串一页给多少、谁先取完，并出来的顺序都和把
 * 所有行一次排好一样，一行不重、一行不丢。
 */
import { describe, expect, it } from 'vitest'

import { type MergeSource, starving, takeReady } from './rankMerge'

type Row = { rank: string; from: string }

/** 把一串已经排好的行切成一页一页（每页 `size` 行），照 `starving` 说的顺序去取。 */
function mergePaged(series: Row[][], sizes: number[]): Row[] {
  const cursors = series.map(() => 0)
  const sources: MergeSource<Row>[] = series.map(() => ({ buffer: [], done: false }))
  const out: Row[] = []
  for (let guard = 0; guard < 1000; guard++) {
    for (const i of starving(sources)) {
      const page = series[i].slice(cursors[i], cursors[i] + sizes[i])
      cursors[i] += page.length
      sources[i].buffer.push(...page)
      sources[i].done = cursors[i] >= series[i].length
    }
    const ready = takeReady(sources)
    out.push(...ready)
    if (!ready.length && !starving(sources).length) return out
  }
  throw new Error('merge did not finish')
}

function ranks(prefix: string, values: number[]): Row[] {
  return values.map((v) => ({ rank: `${prefix}${String(v).padStart(4, '0')}`, from: prefix }))
}

describe('两串翻页并成一张表', () => {
  const files = ranks('1', [1, 4, 5, 9, 12, 13, 20])
  const docs = ranks('1', [2, 3, 6, 7, 8, 30])
  const whole = [...files, ...docs].sort((a, b) => (a.rank < b.rank ? -1 : 1))

  it.each([
    [1, 1],
    [2, 5],
    [3, 1],
    [50, 50],
    [7, 2],
  ])('文件一页 %i 行、文档一页 %i 行：顺序和一次排好的一样', (fileSize, docSize) => {
    expect(mergePaged([files, docs], [fileSize, docSize])).toEqual(whole)
  })

  it('一串是空的，另一串照常一页一页出来', () => {
    expect(mergePaged([files, []], [2, 2])).toEqual(files)
  })

  it('组号小的整组排在前面：文件夹先于任何文件和文档', () => {
    const library = [...ranks('0', [5, 9]), ...ranks('1', [1, 4])]
    const merged = mergePaged([library, docs], [1, 2])
    expect(merged.slice(0, 2).map((row) => row.rank)).toEqual(['00005', '00009'])
    expect(merged).toHaveLength(library.length + docs.length)
  })

  it('一串还没取到下一页时，排在它后面的行先不摆', () => {
    const sources: MergeSource<Row>[] = [
      { buffer: ranks('1', [1, 9]), done: false },
      { buffer: ranks('1', [2]), done: false },
    ]
    // 第二串只给了 2：它的下一页里可能有 3，所以 9 不能先出去。
    expect(takeReady(sources).map((row) => row.rank)).toEqual(['10001', '10002'])
    expect(starving(sources)).toEqual([1])
  })
})
