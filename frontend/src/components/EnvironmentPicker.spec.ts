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
        { spec: 'devices_of:7', label: '张三的设备' },
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
  it('指定电脑只能选自己的：别人的电脑点不了，选自己的那台就发它', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-self_hosted'))
    expect(radio('env-device-theirs').disabled).toBe(true)
    expect(radio('env-device-mine').disabled).toBe(false)

    await fireEvent.update(radio('env-device-mine'))
    await fireEvent.click(submit())
    expect(emitted().select).toEqual([[{ kind: 'self_hosted', device_id: 'mine' }]])
  })

  it('没有自己的电脑就不能指定电脑', () => {
    mount({ kind: 'sandbox', spec: 'standard' }, [DEVICES[1]])
    expect(radio('env-self_hosted').disabled).toBe(true)
  })

  it('沙箱的规格是接口给的那几种，选哪种就发哪种', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-spec-devices_of:7'))
    await fireEvent.click(submit())
    expect(emitted().select).toEqual([[{ kind: 'sandbox', spec: 'devices_of:7' }]])
  })

  it('取消：只说取消，不发选择', async () => {
    const { emitted } = mount()
    await fireEvent.update(radio('env-spec-vm'))
    await fireEvent.click(document.querySelector('[data-testid="env-cancel"]')!)
    expect(emitted().cancel).toHaveLength(1)
    expect(emitted().select).toBeUndefined()
  })
})
