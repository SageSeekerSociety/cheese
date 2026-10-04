/** 设置区块底下那一条的展开与回执（docs/design-system.md §3.11）。这里锁四件：
 *  不脏就不出现、脏了浮出撤销/保存并说「未保存」、保存中就地把按钮锁住、
 *  失败时把原因留在原地。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import SaveBar from './SaveBar.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: Record<string, unknown> = {}) {
  return render(SaveBar, {
    props: { dirty: false, ...props } as never,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('SaveBar', () => {
  it('不脏、也没有回执：整条不出现', () => {
    const view = mount({ dirty: false })
    expect(view.container.querySelector('.save-bar__reveal')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })

  it('脏了：浮出撤销与保存，并说「未保存」', async () => {
    const view = mount({ dirty: true })
    expect(await screen.findByText('Unsaved changes')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Revert' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: 'Revert' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(view.emitted('revert')).toHaveLength(1)
    expect(view.emitted('save')).toHaveLength(1)
  })

  it('保存中：就地写「保存中」，撤销锁住，不再说未保存', async () => {
    mount({ dirty: true, saving: true })
    expect(screen.getByText('Saving…')).toBeTruthy()
    expect(screen.queryByText('Unsaved changes')).toBeNull()
    expect((screen.getByRole('button', { name: 'Revert' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('失败：把服务端原因留在原地', async () => {
    mount({ dirty: true, error: 'HTTP 500' })
    expect(screen.getByText('Save failed: HTTP 500')).toBeTruthy()
    expect(screen.queryByText('Unsaved changes')).toBeNull()
  })

  it('已保存：显示回执，撤销与保存仍在（还没改回去）', async () => {
    mount({ dirty: true, saved: true })
    expect(screen.getByText('Saved')).toBeTruthy()
    expect(screen.queryByText('Unsaved changes')).toBeNull()
  })

  it('表单不合法：保存那颗禁掉，撤销仍可用', async () => {
    mount({ dirty: true, disabled: true })
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Revert' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('调用方可覆盖文案', async () => {
    mount({ dirty: true, note: '有改动', revertLabel: '还原', saveLabel: '储存' })
    expect(screen.getByText('有改动')).toBeTruthy()
    expect(screen.getByRole('button', { name: '还原' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '储存' })).toBeTruthy()
  })
})
