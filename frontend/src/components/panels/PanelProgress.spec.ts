/** 总览里的「进度」：上一轮芝士留下的清单。
 *
 * 它原来挂在对话末尾（「上次的进度」）。现在和看板一样折成一行摘要，点开才是
 * 整张清单；一项都没有的房间里整段不出现。
 */
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getProgress = vi.fn()

vi.mock('../../api', () => ({ getProgress: (...a: unknown[]) => getProgress(...a) }))

import PanelProgress from './PanelProgress.vue'

import { setLocale } from '@/i18n'

const Panel = PanelProgress as unknown as Component

const ROOM = { id: 'room-1', project_id: 'p1', parent_id: null, title: 'r', kind: 'topic', status: 'active' } as Topic

function mount() {
  return render(Panel, {
    props: { topic: ROOM },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  getProgress.mockReset()
})

describe('进度', () => {
  it('折着的时候只说做完了几项，清单点开才有', async () => {
    getProgress.mockResolvedValue({
      items: [
        { id: '1', subject: '梳理数据模型', status: 'completed' },
        { id: '2', subject: '修复可访问性问题', status: 'in_progress' },
        { id: '3', subject: '合并列表', status: 'pending' },
      ],
      updated_at: '2026-09-24T00:00:00Z',
    })
    const { container, findByText, queryByText } = mount()
    await waitFor(() => expect(container.textContent).toContain('完成 1/3'))
    expect(queryByText('修复可访问性问题')).toBeNull()

    await fireEvent.click(container.querySelector('.panel-progress__head') as HTMLElement)
    await findByText('修复可访问性问题')
  })

  it('一项都没有的房间里整段不出现', async () => {
    getProgress.mockResolvedValue({ items: [], updated_at: null })
    const { container } = mount()
    await waitFor(() => expect(getProgress).toHaveBeenCalled())
    expect(container.querySelector('.panel-progress')).toBeNull()
  })
})
