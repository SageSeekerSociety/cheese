// 把几串各自翻页的结果并成一张表，顺序和一次全排好一样。
//
// 资料库页的一张表来自两处：资料库自己（文件夹和文件）和文档表。两处都按同一种位置
// `rank` 排好、一页一页地给（后端 `app/core/rank.py`：按字符串从小到大就是显示的
// 顺序）。合并时，一行只有在「每一串还没取完的，都已经取到比它更靠后的行」时才能
// 摆出来——否则某一串的下一页里可能还有该排在它前面的。所以哪一串手里先空了，就先
// 去取那一串的下一页。

export interface Ranked {
  rank: string
}

export interface MergeSource<T extends Ranked> {
  /** 取回来还没摆出去的行，已经按 rank 排好。 */
  buffer: T[]
  /** 这一串取完了。 */
  done: boolean
}

/** 现在就能摆出去的行，按顺序，并从各串的 buffer 里拿走。一串没取完却已经空了的时候
 *  停下：那一串的下一页还没来，谁排第一说不准。 */
export function takeReady<T extends Ranked>(sources: MergeSource<T>[]): T[] {
  const out: T[] = []
  for (;;) {
    if (sources.some((source) => !source.done && source.buffer.length === 0)) return out
    let first: MergeSource<T> | null = null
    for (const source of sources) {
      const head = source.buffer[0]
      if (head && (!first || head.rank < first.buffer[0].rank)) first = source
    }
    if (!first) return out
    out.push(first.buffer.shift()!)
  }
}

/** 还要去取下一页的那几串：没取完、手里已经空了的。 */
export function starving<T extends Ranked>(sources: MergeSource<T>[]): number[] {
  return sources.flatMap((source, i) => (!source.done && source.buffer.length === 0 ? [i] : []))
}
