// 同一段文字的两版之间，到底改了哪几个字。
//
// 按行比对代码是对的，对文档不是：一段改了两个字，行 diff 只会说「删掉一整段、加上
// 一整段」，人得自己对着两段找不同。文档的版本对比（versionDiff）把配成一对的两段
// 交给这里，标出改了的字。

import { lcsOps } from './lcs'

export type Segment = { text: string; kind: 'same' | 'add' | 'del' }

// 英文和数字按词，中文按字：一个汉字就是一个词，按词切中文切不开。
const TOKEN = /[A-Za-z0-9_]+|\s+|./gsu

/** 同一段的两版里，哪些字没变、哪些删了、哪些加了。 */
export function inlineDiff(before: string, after: string): Segment[] {
  const out: Segment[] = []
  for (const { kind, item } of lcsOps(before.match(TOKEN) ?? [], after.match(TOKEN) ?? [])) {
    const last = out[out.length - 1]
    if (last?.kind === kind) last.text += item
    else out.push({ text: item, kind })
  }
  return out
}
