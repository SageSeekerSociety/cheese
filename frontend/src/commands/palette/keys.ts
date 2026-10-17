// 键盘上的名字在界面上怎么念：Mac 上是 ⌘，别处是 Ctrl。
//
// 顶栏、结果行、底栏三处都要念同一个键，所以认哪台机器只在这里做一次。

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** `mod+k` 念成 `⌘K`（Mac）或 `Ctrl K`。 */
export function shortcutLabel(shortcut: string | undefined): string | undefined {
  if (!shortcut) return undefined
  return shortcut
    .split('+')
    .map((part) =>
      part === 'mod' ? (isMac ? '⌘' : 'Ctrl') : part === 'shift' ? '⇧' : part === 'escape' ? 'Esc' : part.toUpperCase()
    )
    .join(isMac ? '' : ' ')
}

/** 回车前面那个修饰键：按住它回车是在新标签页打开。 */
export const enterKey = isMac ? '⌘' : 'Ctrl'
