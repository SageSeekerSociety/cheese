// 导览的一站：目标登记在页面上才画圈和小窗；按钮把人的意思交出去。
import { defineComponent, h, withDirectives } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { __resetGuideAnchorsForTests, vGuideAnchor } from '@/composables/useGuideAnchors'

import GuideBubble from './GuideBubble.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

// jsdom 不排版：一个固定的框就是一个量得出尺寸的目标；没尺寸的框等于目标不在页面上。
const HERE = new DOMRect(300, 300, 40, 20)
const NOWHERE = new DOMRect(0, 0, 0, 0)

function mount(props: { target: DOMRectReadOnly | null; step?: number; total?: number }) {
  return render(GuideBubble, {
    props: { title: '频道', text: '项目里的对话按频道分开。', ...props },
    global: { plugins: [i18n] },
  })
}

describe('GuideBubble', () => {
  it('draws nothing when its target is not on the page', () => {
    mount({ target: NOWHERE, step: 1, total: 5 })
    expect(document.querySelector('.gb')).toBeNull()
  })

  it('circles the target and lets the person go on, back or skip', async () => {
    const view = mount({ target: HERE, step: 2, total: 5 })
    expect(document.querySelector('.gb__ring')).not.toBeNull()
    expect(view.getByText('2/5')).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: '下一步' }))
    await fireEvent.click(view.getByRole('button', { name: '上一步' }))
    await fireEvent.click(view.getByRole('button', { name: '跳过导览' }))
    expect(Object.keys(view.emitted())).toEqual(expect.arrayContaining(['next', 'back', 'skip']))
  })

  it('a first-time tip has only 知道了', async () => {
    const view = mount({ target: HERE })
    expect(view.queryByRole('button', { name: '下一步' })).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '知道了' }))
    expect(view.emitted().dismiss).toBeTruthy()
  })

  // 这一条要量位置：jsdom 不排版，所以把视口摆好。
  afterEach(() => setViewport(1024, 768))

  it('拖过之后这一站停在人放的地方，走到下一站重新算', async () => {
    setViewport(1280, 900)
    const target = rect(300, 300, 40, 20)
    const view = mount({ target, step: 2, total: 5 })
    const head = document.querySelector('.gb__head') as HTMLElement
    const bubble = document.querySelector('.gb__bubble') as HTMLElement

    // jsdom 量出来的框是 0,0：挪 60×40 之后气泡就停在 (60,40)。
    await fireEvent.pointerDown(head, { button: 0, pointerId: 1, clientX: 100, clientY: 100 })
    await fireEvent.pointerMove(head, { button: 0, pointerId: 1, clientX: 160, clientY: 140 })
    await fireEvent.pointerUp(head, { pointerId: 1 })
    expect(bubble.style.left).toBe('60px')
    expect(bubble.style.top).toBe('40px')

    await view.rerender({ title: '频道', text: '项目里的对话按频道分开。', target, step: 3, total: 5 })
    const next = document.querySelector('.gb__bubble') as HTMLElement
    // 下一站按目标重新算，不带着上一站拖出来的位置。
    expect(next.style.left).not.toBe('60px')
  })
})

function setViewport(width: number, height: number) {
  const w = window as unknown as { innerWidth: number; innerHeight: number }
  w.innerWidth = width
  w.innerHeight = height
}

/** jsdom 里 `new DOMRect(x, y, …)` 的四个边还是 0，所以自己拼一个量得出来的框。 */
function rect(left: number, top: number, width: number, height: number): DOMRectReadOnly {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRectReadOnly
}
