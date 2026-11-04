// 一页正文按「视觉行」排好，给键盘用：上下键一次走一行，Shift 扩选就是一次选几行。
//
// 为什么要先排成行：pdf.js 的文字层是一段段绝对定位的 span，DOM 里的顺序和读的顺序
// 不一定一致（表格、两栏尤其如此），而纯键盘没法像鼠标那样「拖到哪儿算哪儿」。把这一页
// 的文字节点按屏幕上的位置归成行、行内从左到右排，上下键才有一步一行可言。
//
// 这里只管排，不碰选区。选哪几行由调用方定，`rangeOf` 只把行号换算成一个 `Range`。

/** 一块矩形，和 `selectionHighlight` 里的同名类型是一回事，这里不重复导出。 */
export type Box = { left: number; top: number; width: number; height: number }

/** 一个文字节点和它落在屏幕上的那块。 */
export type LineNode = { node: Text; rect: Box }

/** 屏幕上的一行：这一行里有哪几段字，左右上下到哪。 */
export type TextLine = {
  nodes: Text[]
  top: number
  bottom: number
  left: number
  right: number
}

/** 竖直方向搭到较矮那一块的一半以上，就算同一行——和画底色那边同一个判据，
 *  这样「走一行」和「画一条」说的「一行」是同一个东西。 */
const SAME_LINE = 0.5

/** 按屏幕位置把文字节点归成行，行内自左向右。
 *
 *  空白的文字节点不要（pdf.js 在段与段之间会插纯空格），尺寸为零的也不要——那是
 *  量不出来的，放进去会让上下键在某一行上卡住不动。 */
export function linesFrom(nodes: readonly LineNode[]): TextLine[] {
  const usable = nodes.filter(
    (n) => n.rect.width >= 0.5 && n.rect.height >= 0.5 && (n.node.data ?? '').trim().length > 0
  )
  const rows: LineNode[][] = []
  for (const item of [...usable].sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)) {
    const row = rows.find((members) =>
      members.some((member) => {
        const a = member.rect
        const b = item.rect
        const shared = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top)
        return shared > SAME_LINE * Math.min(a.height, b.height)
      })
    )
    if (row) row.push(item)
    else rows.push([item])
  }
  return rows
    .map((members) => {
      const sorted = [...members].sort((a, b) => a.rect.left - b.rect.left)
      return {
        nodes: sorted.map((m) => m.node),
        top: Math.min(...sorted.map((m) => m.rect.top)),
        bottom: Math.max(...sorted.map((m) => m.rect.top + m.rect.height)),
        left: Math.min(...sorted.map((m) => m.rect.left)),
        right: Math.max(...sorted.map((m) => m.rect.left + m.rect.width)),
      }
    })
    .sort((a, b) => a.top - b.top || a.left - b.left)
}

/** 第 `from` 行到第 `to` 行（两头都算，顺序无所谓）这一段文字的 `Range`。
 *
 *  起点在这一段的第一行第一个字之前，终点在最后一行最后一个字之后：选中的是整行，
 *  这正是「一行一格」的意思——按一次上/下，选中的范围就整个换到下一行。 */
export function rangeOf(lines: readonly TextLine[], from: number, to: number): Range | null {
  const start = Math.max(0, Math.min(from, to))
  const end = Math.min(lines.length - 1, Math.max(from, to))
  const first = lines[start]
  const last = lines[end]
  if (!first || !last || !first.nodes.length || !last.nodes.length) return null
  const firstNode = first.nodes[0]
  const lastNode = last.nodes[last.nodes.length - 1]
  const doc = firstNode.ownerDocument
  if (!doc) return null
  const range = doc.createRange()
  range.setStart(firstNode, 0)
  range.setEnd(lastNode, lastNode.data.length)
  return range
}
