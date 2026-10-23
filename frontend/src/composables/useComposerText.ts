/** 输入框正文上两件发消息和改消息共用的事：光标前面那一截，和手打的 @名字 变标签。
 *
 * 光标前面那一截给 @ 补全用：它只看这一截的末尾有没有一个打到一半的 `@`，挑中一项
 * 之后改的也是这一截，光标跟着落在改完的地方——所以在一句话中间也能 @。
 *
 * 手打的「@名字」，打完接一个空格或标点，就地变成标签，和从候选里挑的一样。不等到
 * 发送才换：屏幕上看着 @ 到了，就得真的 @ 到了。只在那个空格或标点刚打出来的时候换——
 * 打到一半的「@张三」也许是在打「@张三丰」。
 */
import type { Ref } from 'vue'

import { computed, watch } from 'vue'

import { textChange } from '../lib/composerDoc'

const BOUNDARY = /[\s,.!?;:，。！？；：、)）]/

export function useComposerText(deps: {
  draft: Ref<string>
  caret: Ref<number>
  /** 「@名字」换成引用的那条规则（发送时用的也是它）。 */
  expand: (text: string) => string
  /** 输入法正在组字：这时候不改正文。 */
  composing: () => boolean
}) {
  const { draft, caret } = deps

  const beforeCaret = computed({
    get: () => draft.value.slice(0, caret.value),
    set: (head: string) => {
      draft.value = head + draft.value.slice(caret.value)
      caret.value = head.length
    },
  })

  watch(draft, (now, before) => {
    const change = textChange(before, now)
    const at = change.from + change.text.length
    if (change.to !== change.from || !change.text || at !== caret.value) return
    if (!BOUNDARY.test(now[at - 1]) || deps.composing()) return
    const head = now.slice(0, at - 1)
    const expanded = deps.expand(head)
    if (expanded === head) return
    draft.value = expanded + now.slice(at - 1)
    caret.value = at + expanded.length - head.length
  })

  return { beforeCaret }
}
