/** 按词跳、按词选、按词删时，正文里的一个 `@某某` 算一个词。
 *
 * 草稿里的 `@` 只是一段普通文字（发出去那一刻才展开成 `<@handle>`），所以浏览器按
 * 自己的分词走：`@` 和后面的名字是两站，名字里有空格（`@Mila Wu`）还会再多一站。
 * 浏览器不认识「提及」，这件事只能由输入框自己接。
 *
 * 跳和选**不替浏览器算下一站在哪**：各平台的按词规则（macOS 停在词尾、Windows 停在
 * 下一个词头、中文按词典切）都留给它，等它走完，落点要是停在一个 `@某某` 中间，就
 * 顺着走的方向推到这个提及的那一头。删是先改选区再让浏览器删：一段选中的字被按词删
 * 时浏览器只删选中的那段，撤销也照旧是浏览器自己那一份。
 *
 * 按词的那个修饰键：macOS 上是 Option，其余平台是 Ctrl。macOS 上 Ctrl+←/→ 是跳到行首
 * 行尾，不归这里管。
 */

export interface TextSpan {
  start: number
  end: number
}

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
}

function isWordKey(e: KeyboardEvent): boolean {
  if (e.metaKey) return false
  return isMac() ? e.altKey && !e.ctrlKey : e.ctrlKey && !e.altKey
}

/** 一行里的空白（不跨换行）：按词删会把词前后的空格一起带走。 */
const INLINE_SPACE = /[^\S\n]/

/** 处理一次 keydown。不是按词的键就什么都不做；从不 preventDefault。 */
export function mentionWordKey(e: KeyboardEvent, spansOf: (text: string) => TextSpan[]): void {
  if (e.isComposing || e.keyCode === 229 || !isWordKey(e)) return
  const box = e.target
  if (!(box instanceof HTMLTextAreaElement)) return
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const forward = e.key === 'ArrowRight'
    // 浏览器的默认动作在这次分发结束之后才挪光标，所以在它之后再看落点。
    setTimeout(() => snapOutOfMention(box, spansOf(box.value), forward), 0)
    return
  }
  if (e.key === 'Backspace' || e.key === 'Delete') {
    if (box.selectionStart !== box.selectionEnd) return // 有选区时删的就是选区
    const range = mentionToDelete(box.value, box.selectionStart, spansOf(box.value), e.key === 'Delete')
    if (range) box.setSelectionRange(range.start, range.end)
  }
}

function snapOutOfMention(box: HTMLTextAreaElement, spans: TextSpan[], forward: boolean) {
  const { selectionStart: start, selectionEnd: end, selectionDirection } = box
  const collapsed = start === end
  // 选区的哪一头是刚挪过的那一头：没有选区时两头是一个点；Shift 往左选时动的是左头。
  const movedStart = !collapsed && selectionDirection === 'backward'
  const pos = movedStart ? start : end
  const inside = spans.find((s) => s.start < pos && pos < s.end)
  if (!inside) return
  const to = forward ? inside.end : inside.start
  if (collapsed) box.setSelectionRange(to, to)
  else if (movedStart) box.setSelectionRange(Math.min(to, end), Math.max(to, end), to <= end ? 'backward' : 'forward')
  else box.setSelectionRange(Math.min(start, to), Math.max(start, to), to >= start ? 'forward' : 'backward')
}

/** 按词删这一下该删掉的整段：光标在一个提及里，或者和它之间只隔着空格。 */
function mentionToDelete(text: string, caret: number, spans: TextSpan[], forward: boolean): TextSpan | null {
  const inside = spans.find((s) => s.start < caret && caret < s.end)
  if (inside) return inside
  let at = caret
  if (forward) {
    while (at < text.length && INLINE_SPACE.test(text[at])) at++
    const next = spans.find((s) => s.start === at)
    return next ? { start: caret, end: next.end } : null
  }
  while (at > 0 && INLINE_SPACE.test(text[at - 1])) at--
  const prev = spans.find((s) => s.end === at)
  return prev ? { start: prev.start, end: caret } : null
}
