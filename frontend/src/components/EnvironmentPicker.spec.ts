import type { Component } from 'vue'
import type { Environment } from '../types/compute'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'

import EnvironmentPicker from './EnvironmentPicker.vue'

const Picker = EnvironmentPicker as unknown as Component

const DEVICES = [
  { device_id: 'mine', name: '我的工作站', online: true, owned: true },
  { device_id: 'theirs', name: '实验室工作站', online: true, owned: false },
]

function mount(current: Environment | null = { kind: 'sandbox', spec: 'standard' }, devices = DEVICES) {
  return render(Picker, {
    props: {
      current,
      devices,
      specs: [
        { spec: 'standard', label: '标准' },
        { spec: 'vm', label: '虚拟机' },
        { spec: 'team_devices', label: '团队设备' },
      ],
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

const radio = (testid: string) => document.querySelector<HTMLInputElement>(`[data-testid="${testid}"] input`)!
const submit = () => document.querySelector<HTMLButtonElement>('[data-testid="env-submit"]')!

beforeEach(() => {
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

describe('环境选择', () => {
  it('only allows choosing a device the reader administers and emits that choice', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-self_hosted'))
    expect(radio('env-device-theirs').disabled).toBe(true)
    expect(radio('env-device-mine').disabled).toBe(false)

    await fireEvent.update(radio('env-device-mine'))
    await fireEvent.click(submit())
    expect(emitted().select).toEqual([[{ kind: 'self_hosted', device_id: 'mine' }]])
  })

  it('disables whole-machine execution when the reader administers no devices', () => {
    mount({ kind: 'sandbox', spec: 'standard' }, [DEVICES[1]])
    expect(radio('env-self_hosted').disabled).toBe(true)
  })

  it('沙箱的规格是接口给的那几种，选哪种就发哪种', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-spec-team_devices'))
    await fireEvent.click(submit())
    expect(emitted().select).toEqual([[{ kind: 'sandbox', spec: 'team_devices' }]])
  })

  // 选规格的地方就是读到它是什么、怎么收费的地方：市场里不再有「模型与环境」那一页。
  it('每种规格旁边写着它是什么、怎么收费', async () => {
    render(Picker, {
      props: {
        current: { kind: 'sandbox', spec: 'standard' },
        devices: DEVICES,
        specs: [
          { spec: 'standard', label: '标准沙箱', description: '每位 AI 队友一个沙箱；按时长计费。' },
          { spec: 'vm', label: '虚拟机沙箱', description: '一整台虚拟机给一位 AI 队友；按规格和时长计费。' },
        ],
      },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    const row = (spec: string) => document.querySelector(`[data-testid="env-spec-${spec}"]`)?.textContent ?? ''
    expect(row('standard')).toContain('按时长计费')
    expect(row('vm')).toContain('按规格和时长计费')
  })

  it('取消：只说取消，不发选择', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-spec-vm'))
    await fireEvent.click(document.querySelector('[data-testid="env-cancel"]')!)
    expect(emitted().cancel).toHaveLength(1)
    expect(emitted().select).toBeUndefined()
  })
})
