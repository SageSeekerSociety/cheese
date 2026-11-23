/** 编辑器里采纳那一条：待审阅时「审阅」和「退回」「采纳」都在条上，点「审阅」是去看改动。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AcceptDockBar from './AcceptDockBar.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')

function mount(props: Record<string, unknown>) {
  return render(AcceptDockBar, {
    props: { title: '待你审阅', icon: 'mdi-circle', color: 'primary', acceptLabel: '采纳', ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('the accept bar in the 编辑器 form', () => {
  it('offers 审阅 beside 退回 and 采纳 when the decision is the reader’s', async () => {
    const { emitted } = mount({ decide: true, reviewAlongside: true })
    expect(screen.getByRole('button', { name: '退回' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '采纳' })).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '审阅' }))
    expect(emitted('review')).toHaveLength(1)
  })

  it('still offers 审阅 when there is nothing to decide yet', () => {
    mount({ decide: false, reviewAlongside: true })
    expect(screen.getByRole('button', { name: '审阅' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '采纳' })).toBeNull()
  })
})
