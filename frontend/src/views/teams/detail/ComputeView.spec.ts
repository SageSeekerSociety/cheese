/**
 * 团队「自有设备」那一屏拆出来的画法（`ComputeView.vue`）：props 进、事件出。
 *
 * 这一半只画：读失败要整块换成失败的样子（不是一边弹条一边画「还没有自有设备」）；
 * 「移出」只给**自己的、且在这个团队里**的机器；别人项目正用着的机器把那句话说出来。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ComputeView from './ComputeView.vue'

import i18n, { setLocale } from '@/i18n'
import { DEVICES, MY_DEVICES, TEAM_DEVICE_ID } from '@/views/demo/catalogScenesMiscFixtures'

const Blank = { template: '<div />' }
const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/settings/devices', name: 'UserSettingsDevices', component: Blank },
      { path: '/:pathMatch(.*)*', component: Blank },
    ],
  })

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。`base` 标成组件自己的 props 类型，字面量（`scope`）就收窄到
 *  `'team' | 'own'`，盖上去的那一份也是同一个类型。 */
type ComputeViewProps = InstanceType<typeof ComputeView>['$props']
const base: ComputeViewProps = {
  scope: 'team',
  loading: false,
  loadFailed: false,
  reason: null,
  forbidden: false,
  addable: [],
  busy: null,
  myDevices: MY_DEVICES,
  devices: [],
  onlineCount: 0,
  teamId: 1,
  myDeviceIds: new Set<string>(),
  error: null,
}

function mount(props: Partial<ComputeViewProps> = {}) {
  return render(ComputeView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n, router()] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('团队自有设备', () => {
  it('还在读的时候画转圈，不画「还没有自有设备」', () => {
    const view = mount({ ...base, loading: true })
    expect(view.container.querySelector('.v-progress-circular')).not.toBeNull()
    expect(view.queryByText('还没有自有设备')).toBeNull()
  })

  it('读失败整块换成失败的样子，点重试请页面重取', async () => {
    const view = mount({ ...base, loadFailed: true, reason: '服务暂时不可用' })
    expect(view.queryByText('服务暂时不可用')).not.toBeNull()
    expect(view.queryByText('还没有自有设备')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().load).toHaveLength(1)
  })

  it('没有权限时说的是没权限，也不给重试', () => {
    const view = mount({ ...base, loadFailed: true, forbidden: true })
    expect(view.queryByText('你没有权限查看')).not.toBeNull()
    expect(view.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('读回来了、一台都没有，才说「还没有自有设备」', () => {
    const view = mount({ ...base, devices: [] })
    expect(view.queryByText('还没有自有设备')).not.toBeNull()
  })

  it('画那一列，并数出在线几台；别的项目占着的机器把那句话说出来', () => {
    const view = mount({
      ...base,
      devices: DEVICES,
      onlineCount: 1,
      myDeviceIds: new Set([TEAM_DEVICE_ID]),
    })
    expect(view.queryByText('书房的 Mac mini')).not.toBeNull()
    expect(view.queryByText('客厅的 NUC')).not.toBeNull()
    expect(view.queryByText('2 台设备 · 1 台在线')).not.toBeNull()
    expect(view.queryByText('仅供 数据清洗 使用')).not.toBeNull()
    expect(view.queryByText('还没有自有设备')).toBeNull()
  })

  it('「移出」只给自己的、且在这个团队里的机器；点了报这一台给页面', async () => {
    const view = mount({
      ...base,
      devices: DEVICES,
      onlineCount: 1,
      myDeviceIds: new Set([TEAM_DEVICE_ID]),
    })
    // 两行里只有第一行（team_ids 含 teamId、且在 myDeviceIds 里）该有这一颗。
    const removes = view.getAllByRole('button', { name: '移出团队' })
    expect(removes).toHaveLength(1)

    await fireEvent.click(removes[0])
    const emitted = view.emitted()['remove-machine'] as unknown[][]
    expect(emitted[0][0]).toMatchObject({ device_id: TEAM_DEVICE_ID })
  })

  it('没在团队里的机器不给「移出」', () => {
    const view = mount({
      ...base,
      devices: [DEVICES[1]],
      onlineCount: 0,
      myDeviceIds: new Set([DEVICES[1].device_id]),
    })
    expect(view.queryByRole('button', { name: '移出团队' })).toBeNull()
  })
})
