/**
 * 后台「飞书应用」那一屏拆出来的画法（`AdminIntegrationsPageView.vue`）：props 进、事件出。
 *
 * 三件会静默坏掉的事，各钉一条：
 * 1. **读失败就只说读不到**：整块换成「读不到」加重试，不画状态行也不画表单 —— 那个
 *    「配没配过」的答案只有读回来才知道。
 * 2. **首屏还没读回来时不回答「还没配置」**：那是给成员看的答案，还没有就先不画。
 * 3. **保存只是往上报一次**：这一半不发请求，App ID 由模型交给页面。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import AdminIntegrationsPageView from './AdminIntegrationsPageView.vue'

import i18n, { setLocale } from '@/i18n'
import { FEISHU_CONFIGURED } from '@/views/demo/catalogScenesMiscFixtures'

function mount(props: Record<string, unknown>) {
  return render(AdminIntegrationsPageView, {
    props,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('飞书应用', () => {
  it('读失败只说读不到，不画状态行也不画表单', () => {
    const view = mount({ ...FEISHU_CONFIGURED, loadError: '飞书应用接口超时' })
    expect(view.queryByText('飞书应用加载失败')).not.toBeNull()
    expect(view.queryByText('飞书应用接口超时')).not.toBeNull()
    expect(view.queryByText('还没配置')).toBeNull()
    expect(view.queryByLabelText('App ID')).toBeNull()
  })

  it('读失败那一下的重试是往上报一次', async () => {
    const view = mount({ ...FEISHU_CONFIGURED, loadError: '' })
    // 原话取不到（空串）也要给出错态，不能落回正常表单。
    expect(view.queryByText('飞书应用加载失败')).not.toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().retry).toHaveLength(1)
  })

  it('首屏还没读回来时不画「还没配置」', () => {
    const view = mount({ ...FEISHU_CONFIGURED, showStatus: false, status: '还没配置' })
    expect(view.queryByText('还没配置')).toBeNull()
    // 但表单已经在这儿了（除了状态行，别的都在）。
    expect(view.queryByLabelText('App ID')).not.toBeNull()
  })

  it('读回来了就说那行答案，状态行那个 data-configured 说的是有没有配过', () => {
    const view = mount({ ...FEISHU_CONFIGURED })
    const status = view.getByText('已配置 · cli_platform · feishu')
    expect(status.getAttribute('data-configured')).toBe('yes')

    view.unmount()
    const unconfigured = mount({ ...FEISHU_CONFIGURED, configured: false, status: '还没配置', updated: '' })
    expect(unconfigured.getByText('还没配置').getAttribute('data-configured')).toBe('no')
  })

  it('保存那颗按钮报的是「保存」，不发请求、也不拦', async () => {
    const view = mount({ ...FEISHU_CONFIGURED })
    await fireEvent.click(view.getByRole('button', { name: '保存' }))
    expect(view.emitted().save).toHaveLength(1)
  })

  it('填 App ID 是把新值交给页面', async () => {
    const view = mount({ ...FEISHU_CONFIGURED })
    await fireEvent.update(view.getByLabelText('App ID') as HTMLInputElement, 'cli_renamed')
    const emitted = view.emitted()['update:appId'] as unknown[][]
    expect(emitted[0][0]).toBe('cli_renamed')
  })

  it('表单没填全时那句话就画在动作上方', () => {
    const view = mount({ ...FEISHU_CONFIGURED, validationError: '先填 App ID' })
    const alert = view.getByText('先填 App ID')
    expect(alert.getAttribute('role')).toBe('alert')
  })
})
