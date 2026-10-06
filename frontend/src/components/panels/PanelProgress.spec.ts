/** 总览里的「进度」：上一轮芝士留下的清单。
 *
 * 它原来挂在对话末尾（「上次的进度」）。现在和看板一样折成一行摘要，点开才是
 * 整张清单；一项都没有的房间里整段不出现。
 *
 * 清单是 props 进来的——取数在 `components/work/PanelOverviewHost.vue`，所以这里
 * 喂数据、不看请求。请求本身（缓存优先、一轮结束重取）在那一层的测试里。
 */
import type { Component } from 'vue'
import type { TodoItem } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import PanelProgress from './PanelProgress.vue'

import { setLocale } from '@/i18n'

const Panel = PanelProgress as unknown as Component

const ITEMS: TodoItem[] = [
  { id: '1', subject: '梳理数据模型', status: 'completed' },
  { id: '2', subject: '修复可访问性问题', status: 'in_progress' },
  { id: '3', subject: '合并列表', status: 'pending' },
]

function mount(items: TodoItem[]) {
  return render(Panel, {
    props: { items },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('进度', () => {
  it('折着的时候只说做完了几项，清单点开才有', async () => {
    const { container, findByText, queryByText } = mount(ITEMS)
    expect(container.textContent).toContain('完成 1/3')
    expect(queryByText('修复可访问性问题')).toBeNull()

    await fireEvent.click(container.querySelector('.panel-progress__head') as HTMLElement)
    await findByText('修复可访问性问题')
  })

  it('每次点开都展开着进来，不只是第一次', async () => {
    // 测试库默认把 Transition 换成直接出现的桩，这里要的恰恰是它。
    const { container } = render(Panel, {
      props: { items: ITEMS },
      global: { plugins: [createVuetify({ components, directives })], stubs: { transition: false } },
    })
    expect(container.querySelector('.panel-progress__head')).not.toBeNull()
    const head = container.querySelector('.panel-progress__head') as HTMLElement
    for (let round = 0; round < 2; round++) {
      await fireEvent.click(head)
      const fold = container.querySelector('.progress-fold') as HTMLElement
      expect(fold.className, `open #${round + 1}`).toMatch(/enter-active/)
      await fireEvent.click(head)
    }
  })

  it('一项都没有的房间里整段不出现', () => {
    const { container } = mount([])
    expect(container.querySelector('.panel-progress')).toBeNull()
  })
})
