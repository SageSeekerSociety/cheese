// 对话栏和工作面板怎么分：没拖过的人窄桌面对半，宽屏把多出来的给预览；双击回到这一份。
import { ref } from 'vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { useLayout } from './layout'

const original = window.innerWidth
function screen(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
}

beforeEach(() => localStorage.clear())
afterEach(() => screen(original))

it('没拖过：1280 宽对半分，1920 宽对话栏让出 5%', () => {
  screen(1280)
  expect(useLayout(ref(null)).chatPct.value).toBe(50)
  screen(1920)
  expect(useLayout(ref(null)).chatPct.value).toBe(45)
})

it('拖过就记住；双击复位回到这块屏幕的默认值', () => {
  screen(1920)
  const layout = useLayout(ref(null))
  layout.setChatPct(70)
  expect(useLayout(ref(null)).chatPct.value).toBe(70)
  layout.resetChatPct()
  expect(layout.chatPct.value).toBe(45)
})
