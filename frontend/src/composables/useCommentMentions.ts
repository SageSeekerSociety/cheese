// 文档评论框里的 `@`：和对话输入框是同一份候选、同一块菜单（useRoomMentionPicker +
// MentionMenu），只是没有资料库、话题和群播——评论里 @ 的是人，和文档的 AI 队友。
//
// 菜单贴在输入框**下面**：新评论的框在评论栏最上面，浮到它上面就被栏头盖住了。
// 位置按输入框在它那个 `position: relative` 的盒子里的位置算（MentionMenu 的 `at`）。
import type { Ref } from 'vue'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { nextTick, ref, watch } from 'vue'

import { useRoomMentionPicker } from './useRoomMentionPicker'

export function useCommentMentions(deps: {
  /** 框里的字。挑中一个人之后由这里改它。 */
  text: Ref<string>
  /** 能 @ 的人：项目里的人和文档的 AI 队友。 */
  people: () => MentionPoolEntry[]
  input: Ref<HTMLTextAreaElement | null>
}) {
  const menu = ref<{ scrollActiveIntoView: () => void } | null>(null)
  const picker = useRoomMentionPicker({
    draft: deps.text,
    topic: () => null,
    mentionPool: deps.people,
    topicList: () => [],
    focus: () => deps.input.value?.focus(),
    broadcast: false,
    scrollActiveIntoView: () => menu.value?.scrollActiveIntoView(),
  })

  const at = ref<{ top: number; left: number; width: number } | null>(null)
  function place() {
    const el = deps.input.value
    at.value = el ? { top: el.offsetTop + el.offsetHeight + 4, left: el.offsetLeft, width: el.offsetWidth } : null
  }
  watch([picker.menuVisible, deps.text], () => void nextTick(place))

  /** 菜单开着时它接走的键：↑/↓ 换高亮、回车挑人、Esc 收起。接走了返回 true，评论框
   *  自己的回车发送、Esc 收起就不再做。输入法选字时这些键是给输入法的。 */
  function onKey(e: KeyboardEvent): boolean {
    if (e.isComposing || e.keyCode === 229) return false
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && picker.menuOpen.value) {
      e.preventDefault()
      picker.move(e.key === 'ArrowDown' ? 1 : -1)
      return true
    }
    if (e.key === 'Escape' && picker.menuVisible.value) {
      e.preventDefault()
      e.stopPropagation()
      picker.close()
      return true
    }
    if (e.key === 'Enter' && !e.shiftKey && picker.pickActive()) {
      e.preventDefault()
      return true
    }
    return false
  }

  return { menu, at, onKey, ...picker }
}
