/** 队友的步骤清单消息：一步一行，✱ 加粗是正在做的、○ 是还没做的、✓ 淡下去是做完的；
 * 做完时下面接一句结果；最底下一行说清单什么时候更新的。 */
import type { ChecklistMeta } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChecklistMessage from './ChecklistMessage.vue'

import { setLocale } from '@/i18n'

const NOW = new Date('2026-09-20T14:40:00')

const list: ChecklistMeta = {
  items: [
    { id: '1', subject: '核实问题', status: 'completed' },
    { id: '2', subject: '写实现', status: 'in_progress' },
    { id: '3', subject: '补测试', status: 'pending' },
  ],
  result: null,
}

function mount(props: { checklist?: ChecklistMeta; updatedAt: string; edited?: boolean; live?: boolean }) {
  return render(ChecklistMessage, {
    props: { checklist: list, edited: false, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function row(view: ReturnType<typeof mount>, subject: string): HTMLElement {
  return view.getByText(subject).closest('li') as HTMLElement
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => vi.useRealTimers())

describe('步骤清单消息', () => {
  it('每一步一个记号：正在做的是 ✱，还没做的是 ○，做完的是 ✓', () => {
    const view = mount({ updatedAt: NOW.toISOString() })
    expect(row(view, '写实现').querySelector('.mdi-asterisk')).not.toBeNull()
    expect(row(view, '补测试').querySelector('.mdi-circle-outline')).not.toBeNull()
    expect(row(view, '核实问题').querySelector('.mdi-check')).not.toBeNull()
    expect(row(view, '写实现').className).toContain('is-in_progress')
    expect(row(view, '核实问题').className).toContain('is-completed')
  })

  // 队友还在推进时，正在做的那一步换成一颗在动的星；这一轮停了（或是更早的一张），
  // 就只剩静止的 ✱——没有在发生的事，就不该动。
  it('队友正在推进时，正在做的那一步在动；别的步骤不动', () => {
    const view = mount({ updatedAt: NOW.toISOString(), live: true })
    expect(row(view, '写实现').classList).toContain('is-live')
    expect(row(view, '写实现').querySelector('.mdi-asterisk')).toBeNull()
    expect(row(view, '写实现').querySelector('svg')).not.toBeNull()
    expect(row(view, '补测试').classList).not.toContain('is-live')
    expect(row(view, '核实问题').classList).not.toContain('is-live')
  })

  it('停下来的清单不动', () => {
    const view = mount({ updatedAt: NOW.toISOString(), live: false })
    expect(row(view, '写实现').classList).not.toContain('is-live')
    expect(row(view, '写实现').querySelector('.mdi-asterisk')).not.toBeNull()
  })

  it('做完时下面接一句结果', () => {
    const view = mount({ checklist: { ...list, result: '接口改好了，测试全过' }, updatedAt: NOW.toISOString() })
    expect(view.getByText('接口改好了，测试全过')).toBeTruthy()
  })

  it('刚更新过说「刚刚」，过一阵换成几分钟前', async () => {
    const view = mount({ updatedAt: NOW.toISOString() })
    expect(view.getByText('清单更新于 刚刚')).toBeTruthy()
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    expect(view.getByText('清单更新于 5分钟前')).toBeTruthy()
  })

  it('一小时以前的说钟点，改过的也标「已编辑」', () => {
    const at = new Date('2026-09-20T09:05:00')
    const view = mount({ updatedAt: at.toISOString(), edited: true })
    const clock = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    expect(view.getByText(`清单更新于 ${clock}`)).toBeTruthy()
    expect(view.getByText('已编辑')).toBeTruthy()
  })
})
