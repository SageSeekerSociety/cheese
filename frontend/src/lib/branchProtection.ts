// 分支保护 (#718) 设置区的输入解析。独立成模块：这两个函数是纯逻辑，
// 不该埋在视图里没法测。

/** 路径范围输入框 → glob 列表：按逗号（中英文）和空白切分、去空、去重。 */
export function parseCheckPaths(text: string): string[] {
  const out: string[] = []
  for (const piece of text.split(/[,，\s]+/)) {
    const p = piece.trim()
    if (p && !out.includes(p)) out.push(p)
  }
  return out
}

/** 批准人数输入 → 不小于 1 的整数；非法输入返回 null。 */
export function parseApprovalsInput(text: string): number | null {
  const t = String(text).trim()
  if (!/^\d+$/.test(t)) return null
  const n = Number(t)
  return n >= 1 ? n : null
}
