/**
 * 后台「外站题账号」那一屏拆出来的画法（`AdminExternalProblemPageView.vue`）：props 进、事件出。
 *
 * 三件会静默坏掉的事，各钉一条：
 * 1. **读失败就只说读不到**：整块换成「读不到」加重试，不画状态行也不画表单 —— 那个
 *    「配没配过」的答案只有读回来才知道。
 * 2. **首屏还没读回来时不回答「还没配置」**：那是读回来才有的答案，还没有就先不画。
 * 3. **保存只是往上报一次**：这一半不发请求，账号由模型交给页面。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import AdminExternalProblemPageView from './AdminExternalProblemPageView.vue'

import i18n, { setLocale } from '@/i18n'

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。这一份就是「已经配过」那一种形状。 */
type AdminExternalProblemPageViewProps = InstanceType<typeof AdminExternalProblemPageView>['$props']
const base: AdminExternalProblemPageViewProps = {
  loading: false,
  loadError: null,
  status: '已配置 · teacher01 · yoj',
  showStatus: true,
  configured: true,
  updated: '最后由 andy 更新',
  saving: false,
  saved: false,
  saveError: '',
  validationError: '',
  username: 'teacher01',
  password: '',
}

function mount(props: Partial<AdminExternalProblemPageViewProps> = {}) {
  return render(AdminExternalProblemPageView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('外站题账号', () => {
  it('读失败只说读不到，不画状态行也不画表单', () => {
    const view = mount({ loadError: '外站题账号接口超时' })
    expect(view.queryByText('外站题账号加载失败')).not.toBeNull()
    expect(view.queryByText('外站题账号接口超时')).not.toBeNull()
    expect(view.queryByText('还没配置')).toBeNull()
    expect(view.queryByLabelText('账号')).toBeNull()
  })

  it('读失败那一下的重试是往上报一次', async () => {
    const view = mount({ loadError: '' })
    // 原话取不到（空串）也要给出错态，不能落回正常表单。
    expect(view.queryByText('外站题账号加载失败')).not.toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().retry).toHaveLength(1)
  })

  it('首屏还没读回来时不画「还没配置」', () => {
    const view = mount({ showStatus: false, status: '还没配置' })
    expect(view.queryByText('还没配置')).toBeNull()
    // 但表单已经在这儿了（除了状态行，别的都在）。
    expect(view.queryByLabelText('账号')).not.toBeNull()
  })

  it('读回来了就说那行答案，状态行那个 data-configured 说的是有没有配过', () => {
    const view = mount()
    const status = view.getByText('已配置 · teacher01 · yoj')
    expect(status.getAttribute('data-configured')).toBe('yes')

    view.unmount()
    const unconfigured = mount({ configured: false, status: '还没配置', updated: '' })
    expect(unconfigured.getByText('还没配置').getAttribute('data-configured')).toBe('no')
  })

  it('保存那颗按钮报的是「保存」，不发请求、也不拦', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '保存' }))
    expect(view.emitted().save).toHaveLength(1)
  })

  it('填账号是把新值交给页面', async () => {
    const view = mount()
    await fireEvent.update(view.getByLabelText('账号') as HTMLInputElement, 'teacher02')
    const emitted = view.emitted()['update:username'] as unknown[][]
    expect(emitted[0][0]).toBe('teacher02')
  })

  it('表单没填全时那句话就画在动作上方', () => {
    const view = mount({ validationError: '先填账号' })
    const alert = view.getByText('先填账号')
    expect(alert.getAttribute('role')).toBe('alert')
  })
})
