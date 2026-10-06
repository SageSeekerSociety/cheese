/**
 * 「飞书应用」这一页**画的那一半**：读失败、状态行、表单校验、保存回执。
 *
 * 读失败那一档是这一组最要紧的：判据是 `loadError !== null` 而不是真值 —— 服务端原话
 * 取不到时它是空串，仍要画出错态，**不**接着画「还没配置」和那张表单（配没配是读回来
 * 才知道的事，读失败就意味着这个问题还没有答案）。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key }),
  }
})

import AdminIntegrationsPageView from './AdminIntegrationsPageView.vue'

const BASE = {
  loading: false,
  loadError: null,
  validationError: '',
  saving: false,
  saved: false,
  saveError: null,
  appId: 'cli_1',
  appSecret: '',
  domain: 'feishu',
  domains: [
    { value: 'feishu', title: 'Feishu' },
    { value: 'lark', title: 'Lark' },
  ],
  status: '已配置',
  updated: '2026-10-01 12:00',
  showStatus: true,
  configured: true,
}

function mount(props: Record<string, unknown> = {}) {
  return render(AdminIntegrationsPageView, {
    props: { ...BASE, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('飞书应用页的画面', () => {
  it('读失败时画错误行和重试，不画「还没配置」和那张表单', () => {
    const { getByText, queryByText } = mount({ loadError: '' })

    expect(getByText('integrations.admin.loadFailed')).toBeTruthy()
    expect(getByText('integrations.admin.retry')).toBeTruthy()
    expect(queryByText('integrations.admin.save')).toBeNull()
    expect(queryByText('已配置')).toBeNull()
  })

  it('读失败时重试那颗按钮往上发一次 retry', async () => {
    const { emitted, getByText } = mount({ loadError: '服务器 500' })

    await fireEvent.click(getByText('integrations.admin.retry'))
    expect(emitted('retry')).toEqual([[]])
  })

  it('读回来了才画状态行，并按 configured 标出来', () => {
    const { getByText } = mount({ configured: false, status: '还没配置' })

    const line = getByText('还没配置')
    expect(line.closest('p')?.getAttribute('data-configured')).toBe('no')
  })

  it('首屏还没读回来时不画状态行（还没有答案）', () => {
    const { queryByText } = mount({ showStatus: false })

    expect(queryByText('已配置')).toBeNull()
  })

  it('填写不合格时的说明画在表单里，不当成读失败', () => {
    const { getByText, queryByText } = mount({ validationError: '先填 App ID' })

    expect(getByText('先填 App ID')).toBeTruthy()
    expect(queryByText('integrations.admin.loadFailed')).toBeNull()
  })

  it('保存那颗按钮往上发一次 save', async () => {
    const { emitted, getByText } = mount()

    await fireEvent.click(getByText('integrations.admin.save'))
    expect(emitted('save')).toEqual([[]])
  })

  it('上一行的时效戳照画', () => {
    const { getByText } = mount({ updated: '2026-10-01 12:00' })

    expect(getByText('2026-10-01 12:00')).toBeTruthy()
  })
})
