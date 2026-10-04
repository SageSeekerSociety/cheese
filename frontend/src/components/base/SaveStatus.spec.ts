/** 就地回执的三种样子和读屏角色（docs/design-system.md §3.11）。这里锁四件：
 *  三态各自的文案与颜色类、失败要用 role=alert、成功/进行中用 role=status、
 *  空闲时什么都不画。 */
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import SaveStatus from './SaveStatus.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: Record<string, unknown> = {}) {
  return render(SaveStatus, { props })
}

describe('SaveStatus', () => {
  it('空闲：什么都不画', () => {
    const view = mount()
    expect(view.container.textContent?.trim()).toBe('')
    expect(view.container.querySelector('.save-status')).toBeNull()
  })

  it('已保存：默认文案 + role=status', () => {
    const view = mount({ saved: true })
    const el = view.container.querySelector('.save-status')
    expect(el?.textContent?.trim()).toBe('Saved')
    expect(el?.getAttribute('role')).toBe('status')
    expect(el?.classList.contains('save-status--saved')).toBe(true)
  })

  it('保存中：文案 + role=status', () => {
    const view = mount({ saving: true })
    const el = view.container.querySelector('.save-status')
    expect(el?.textContent?.trim()).toBe('Saving…')
    expect(el?.getAttribute('role')).toBe('status')
  })

  it('失败带原因：用冒号接上原因，且 role=alert', () => {
    const view = mount({ error: 'HTTP 500' })
    const el = view.container.querySelector('.save-status')
    expect(el?.textContent?.trim()).toBe('Save failed: HTTP 500')
    expect(el?.getAttribute('role')).toBe('alert')
    expect(el?.classList.contains('save-status--error')).toBe(true)
  })

  it('失败但原因和兜底那句一样时，不重复', () => {
    const view = mount({ error: 'Save failed' })
    expect(screen.getByText('Save failed')).toBeTruthy()
    expect(view.container.textContent?.trim()).toBe('Save failed')
  })

  it('失败优先于保存中与已保存', () => {
    const view = mount({ error: 'HTTP 500', saving: true, saved: true })
    expect(view.container.querySelector('.save-status--error')).toBeTruthy()
  })

  it('调用方可覆盖成功文案', () => {
    const view = mount({ saved: true, savedText: '设置已保存' })
    expect(view.container.querySelector('.save-status')?.textContent?.trim()).toBe('设置已保存')
  })
})
