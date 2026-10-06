/** 环境页上那一次失败：能让芝士看看、能重试；它的改法要人采用才生效。 */
import type { Component } from 'vue'
import type { EnvironmentDiagnosis, EnvironmentFailure } from '@/types/environment'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import EnvironmentFailureCard from './EnvironmentFailureCard.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const FAILURE: EnvironmentFailure = {
  attempt: 'a1',
  at: '2026-10-07T06:02:00Z',
  stage: 'setup',
  exit_code: 1,
  log: 'ERR_PNPM_UNSUPPORTED_ENGINE',
  waiting: 1,
}

const ANSWER: EnvironmentDiagnosis = {
  reason: '前端要求 Node 22',
  change: '装依赖之前切到 Node 22',
  setup_script: 'cd frontend\nmise use node@22\npnpm install',
  startup_script: null,
  sure: true,
  ran: { setup_script: 'cd frontend\npnpm install', startup_script: '' },
}

function mount(props: Partial<Record<string, unknown>> = {}) {
  return render(EnvironmentFailureCard as unknown as Component, {
    props: {
      failure: FAILURE,
      roomTitle: '前端',
      canEdit: true,
      agentName: '芝士',
      diagnosis: null,
      diagnosing: false,
      retrying: false,
      ...props,
    },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('一次准备失败', () => {
  it('能编辑的人可以让 AI 队友看看，也可以重试', async () => {
    const ui = mount()
    const diagnose = ui.getByTestId('environment-diagnose')
    expect(diagnose.textContent).toContain('让芝士看看')
    await fireEvent.click(diagnose)
    await fireEvent.click(ui.getByRole('button', { name: '重试' }))
    expect(ui.emitted().diagnose).toHaveLength(1)
    expect(ui.emitted().retry).toHaveLength(1)
  })

  it('只能看的人没有这两颗按钮', () => {
    const ui = mount({ canEdit: false })
    expect(ui.queryByTestId('environment-diagnose')).toBeNull()
    expect(ui.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('改法标出加的那一行，采用了才生效', async () => {
    const ui = mount({ diagnosis: ANSWER })
    expect(ui.getByTestId('environment-diagnosis').textContent).toContain('+ mise use node@22')
    expect(ui.emitted().adopt).toBeUndefined()
    await fireEvent.click(ui.getByRole('button', { name: '采用并重试' }))
    expect(ui.emitted().adopt).toHaveLength(1)
  })

  it('没有要改的脚本时，没有可采用的东西', () => {
    const ui = mount({ diagnosis: { ...ANSWER, setup_script: null } })
    expect(ui.queryByRole('button', { name: '采用并重试' })).toBeNull()
  })
})
