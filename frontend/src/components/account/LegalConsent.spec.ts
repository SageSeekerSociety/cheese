/** 注册类页面的同意环节（#1486）：默认不勾；不勾就提交，弹一次「请阅读并同意」，
 * 点同意 = 勾上并继续，点取消 = 不提交。提交出去的同意要带后端给的版本和方式。 */
import { defineComponent, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LegalConsent from './LegalConsent.vue'

import { setLocale } from '@/i18n'

const listDocuments = vi.fn()
vi.mock('@/network/api/legal', () => ({ LegalApi: { listDocuments: () => listDocuments() } }))

const Host = defineComponent({
  components: { LegalConsent },
  setup() {
    const consent = ref<InstanceType<typeof LegalConsent> | null>(null)
    const result = ref<unknown>('none')
    const submit = async () => {
      result.value = await consent.value?.confirm()
    }
    return { consent, result, submit }
  },
  template: `<div>
    <LegalConsent ref="consent" action-label="同意并注册" />
    <button @click="submit">提交</button>
    <output data-testid="result">{{ JSON.stringify(result) }}</output>
  </div>`,
})

function mount() {
  const page = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/legal/terms', name: 'LegalTerms', component: page },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: page },
      { path: '/:p(.*)*', component: page },
    ],
  })
  return render(Host, { global: { plugins: [router, createVuetify({ components, directives })] } })
}

const result = () => JSON.parse(screen.getByTestId('result').textContent || '"none"')

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  listDocuments.mockReset()
  listDocuments.mockResolvedValue({
    data: {
      documents: [
        { document: 'terms', title: '用户协议', version: '1.0', effectiveDate: '2026-09-23' },
        { document: 'privacy', title: '隐私政策', version: '1.2', effectiveDate: '2026-09-23' },
      ],
    },
  })
})

afterEach(() => vi.unstubAllGlobals())

describe('LegalConsent', () => {
  it('starts unticked', () => {
    mount()
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false)
  })

  it('a ticked box submits straight away, as a checkbox consent to the current versions', async () => {
    mount()
    await fireEvent.input(screen.getByRole('checkbox'), { target: { checked: true } })
    await fireEvent.click(screen.getByText('提交'))
    await waitFor(() => expect(result()).toEqual({ documents: { terms: '1.0', privacy: '1.2' }, method: 'checkbox' }))
    expect(screen.queryByText('请阅读并同意以下条款')).toBeNull()
  })

  it('submitting unticked asks once; agreeing ticks the box and continues', async () => {
    mount()
    await fireEvent.click(screen.getByText('提交'))
    await fireEvent.click(await screen.findByRole('button', { name: '同意并注册' }))
    await waitFor(() => expect(result()).toEqual({ documents: { terms: '1.0', privacy: '1.2' }, method: 'dialog' }))
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true)
  })

  it('cancelling the prompt submits nothing', async () => {
    mount()
    await fireEvent.click(screen.getByText('提交'))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))
    await waitFor(() => expect(result()).toBeNull())
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false)
  })

  it('without the current versions nothing is submitted', async () => {
    listDocuments.mockRejectedValue(new Error('offline'))
    mount()
    await fireEvent.input(screen.getByRole('checkbox'), { target: { checked: true } })
    await fireEvent.click(screen.getByText('提交'))
    await waitFor(() => expect(result()).toBeNull())
    expect(screen.getByText('暂时无法获取协议，请刷新页面重试')).toBeTruthy()
  })
})
