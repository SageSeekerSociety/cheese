/**
 * 管理员看板「告警」那一格拆出来的画法（`AlertsView.vue`）：props 进、事件出。
 *
 * 这一格只有一件事：把六个待办计数画出来，能处理的数是一颗按钮、点一下把对应的筛选
 * 带到「题目」那一格。所以测的是：读回来的六个数是六个、能处理的那三个才是按钮、
 * 「还没读回来」画进度条而不是「暂无告警」、「读不到」说清原因并给重试、只有真读到
 * 了空才说「暂无告警」。取数与跳转在页面（`Alerts.vue`），这里一行都不碰。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import AlertsView from './AlertsView.vue'

import i18n, { setLocale } from '@/i18n'
import { ANALYTICS_ALERTS } from '@/views/demo/catalogScenesMisc2Fixtures'

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。`base` 标成组件自己的 props 类型。 */
type AlertsViewProps = InstanceType<typeof AlertsView>['$props']
const base: AlertsViewProps = {
  loading: false,
  failed: false,
  errorDetail: null,
  alerts: ANALYTICS_ALERTS,
}

function mount(props: Partial<AlertsViewProps> = {}) {
  return render(AlertsView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('告警', () => {
  it('还在读的时候画进度条，不说「暂无告警」', () => {
    const view = mount({ ...base, loading: true, alerts: null })
    expect(view.container.querySelector('.v-progress-linear')).not.toBeNull()
    expect(view.queryByText('暂无告警')).toBeNull()
  })

  it('读失败只说读不到，点重试请页面重取', async () => {
    const view = mount({ ...base, failed: true, errorDetail: '告警接口超时', alerts: null })
    expect(view.queryByText('加载告警失败')).not.toBeNull()
    expect(view.queryByText('告警接口超时')).not.toBeNull()
    expect(view.queryByText('暂无告警')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().load).toHaveLength(1)
  })

  it('读回来了、确实一个待办都没有，才说「暂无告警」', () => {
    const view = mount({ ...base, alerts: null })
    expect(view.queryByText('暂无告警')).not.toBeNull()
  })

  it('六个计数各自画出来，每个都带自己的名字', () => {
    const view = mount()
    expect(view.queryByText('待审核题目')).not.toBeNull()
    expect(view.queryByText('停滞题目')).not.toBeNull()
    expect(view.queryByText('长期不活跃出题人')).not.toBeNull()
    expect(view.queryByText('暂无告警')).toBeNull()
  })

  it('能处理的三个是按钮，其余三个只是数字', () => {
    const view = mount()
    const buttons = view.container.querySelectorAll('.alert--open')
    expect(buttons).toHaveLength(3)
    expect(view.getAllByRole('button')).toHaveLength(3)
  })

  it('点一个能处理的数，报上去的是「跳到哪一格、带哪几个筛选」', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: /待审核题目/ }))
    const emitted = view.emitted().openTasks as unknown[][]
    expect(emitted[0][0]).toEqual({ taskApproved: 'NONE' })

    await fireEvent.click(view.getByRole('button', { name: /待审核申请/ }))
    const next = view.emitted().openTasks as unknown[][]
    expect(next[1][0]).toEqual({ hasPendingApproval: true })
  })
})
