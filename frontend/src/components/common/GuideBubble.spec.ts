// 导览的一站：目标登记在页面上才画圈和小窗；按钮把人的意思交出去。
import { defineComponent, h, withDirectives } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

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
})
