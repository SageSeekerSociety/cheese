/** 项目设置「任务命名」：只吃 props、只往上发事件（A 级）。
 *
 *  取数与发请求不在这一件里（在 `composables/useProjectTaskNamingSettings.ts`，那里
 *  另有一份行为测试）：这里给状态、看点出来的样子，点一下看报上去的那一档。
 */
import type { TaskNaming } from '../api'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'

import ProjectTaskNamingSettings from './ProjectTaskNamingSettings.vue'

const UI = { plugins: [createVuetify({ components, directives })] }

function naming(overrides: Partial<TaskNaming> = {}): TaskNaming {
  return { mode: 'auto', available: true, ...overrides }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(props: { state: TaskNaming | null; error?: string; busy?: boolean }) {
  return render(ProjectTaskNamingSettings, {
    props: { error: '', busy: false, ...props },
    global: UI,
  })
}

describe('ProjectTaskNamingSettings', () => {
  it('shows the current mode and reports a pick upward', async () => {
    const view = mount({ state: naming() })
    const auto = await view.findByRole('radio', { name: /智能命名/ })
    expect(auto.getAttribute('aria-checked')).toBe('true')
    await fireEvent.click(view.getByRole('radio', { name: /手动命名/ }))
    expect(view.emitted('choose')).toEqual([['manual']])
  })

  it('marks the manual mode when that is what it reads', async () => {
    const view = mount({ state: naming({ mode: 'manual' }) })
    expect((await view.findByRole('radio', { name: /手动命名/ })).getAttribute('aria-checked')).toBe('true')
  })

  it('disables the radios while a save is on its way', async () => {
    const view = mount({ state: naming(), busy: true })
    const manual = (await view.findByRole('radio', { name: /手动命名/ })) as HTMLButtonElement
    expect(manual.disabled).toBe(true)
  })

  it('says why when there was no answer', async () => {
    const view = mount({ state: null, error: '读不出来了' })
    expect(view.getByText('读不出来了')).toBeTruthy()
  })

  it('mentions the fallback when auto cannot run here', async () => {
    const view = mount({ state: naming({ available: false }) })
    expect(view.getByText(/无法自动起名/)).toBeTruthy()
  })
})
