// 一组 `role="tab"` 的键盘语义（WAI-ARIA Authoring Practices 的 Tabs 模式）：
//
// - roving tabindex：整组只有选中的那一格在 Tab 序列里（tabindex=0），其余是 -1——按一次
//   Tab 进到这一组，再按一次就离开，而不是挨个走过每一格；
// - ←/→ 在格子之间移动并选中（自动激活：这些页签切换都是本地的，不发请求、不丢输入），
//   Home / End 到头尾。
//
// 写成指令挂在 `role="tablist"` 那个元素上：各处页签的模板和点击逻辑不用改，只要多一个
// `v-roving-tabs`。选中哪一格仍由各自的 `aria-selected` 说了算，指令只读它。
import type { Directive } from 'vue'

function tabsOf(list: HTMLElement): HTMLElement[] {
  return Array.from(list.querySelectorAll<HTMLElement>('[role="tab"]')).filter(
    (tab) => tab.closest('[role="tablist"]') === list && !tab.hasAttribute('disabled')
  )
}

function syncTabIndex(list: HTMLElement): void {
  const tabs = tabsOf(list)
  const selected = tabs.find((tab) => tab.getAttribute('aria-selected') === 'true') ?? tabs[0]
  for (const tab of tabs) tab.tabIndex = tab === selected ? 0 : -1
}

function onKeydown(event: KeyboardEvent): void {
  const list = event.currentTarget as HTMLElement
  const tabs = tabsOf(list)
  const from = tabs.indexOf(document.activeElement as HTMLElement)
  if (from < 0 || event.altKey || event.ctrlKey || event.metaKey) return
  const n = tabs.length
  let to: number
  switch (event.key) {
    case 'ArrowRight':
      to = (from + 1) % n
      break
    case 'ArrowLeft':
      to = (from - 1 + n) % n
      break
    case 'Home':
      to = 0
      break
    case 'End':
      to = n - 1
      break
    default:
      return
  }
  event.preventDefault()
  tabs[to].focus()
  tabs[to].click()
}

export const vRovingTabs: Directive<HTMLElement> = {
  mounted(el) {
    syncTabIndex(el)
    el.addEventListener('keydown', onKeydown)
  },
  updated(el) {
    syncTabIndex(el)
  },
  unmounted(el) {
    el.removeEventListener('keydown', onKeydown)
  },
}
