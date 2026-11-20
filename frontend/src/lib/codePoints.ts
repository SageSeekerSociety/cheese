// 按字（码点）数的一小段文字。
//
// 指针那几处的预告里写着「前 200 字」，表格一片的内容截在 2000 字。用
// `String.prototype.slice` 按 UTF-16 下标切，看着也对得上那个数 —— 直到切点落在代理对
// 中间：一个 emoji 占两个下标，切一半出来是半个字符，屏幕上渲染成一个问号。按码点切
// 没有这个问题。
//
// 上限只在展示用，不影响指出去的东西本身（那些都是完整的）。

/** 文字的开头 `max` 个字，收下了几个字，以及**是不是把整段都收下了**。
 *
 *  一边接一边算的东西（表格里把一片格子摊平）要后两样：知道收下几个字才能接着算还剩
 *  多少额度，接得完整段才接着接下一段。
 *
 *  一遍扫过去，够了就停：代价不随整段的长短走（最多 `max` 步），也不额外分配一整段
 *  的数组 —— 一整格可能有一百万个字，先摊成数组再切就白费了上限这一回事。 */
export function takeCodePoints(text: string, max: number): { text: string; points: number; complete: boolean } {
  let cut = 0
  let points = 0
  while (cut < text.length && points < max) {
    cut += text.codePointAt(cut)! > 0xffff ? 2 : 1
    points += 1
  }
  const complete = cut >= text.length
  return { text: complete ? text : text.slice(0, cut), points, complete }
}

/** 文字的开头 `max` 个字。短于 `max` 就原样返回。 */
export function firstCodePoints(text: string, max: number): string {
  return takeCodePoints(text, max).text
}
