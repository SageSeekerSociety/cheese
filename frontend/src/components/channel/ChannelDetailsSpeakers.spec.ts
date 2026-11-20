// 频道详情里的「谁能发言」（#4795）。错了也照样渲染、却会把频道设错的几条：
//   1. 从「所有人」改成「指定角色」，先交出管理员和成员两个，频道不会一下子谁也说不了话
//   2. 勾掉最后一个角色不交出去：至少留一个
//   3. 支线开关只在限了角色时出现，拨它时角色名单原样带上
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import ChannelDetailsDialog from './ChannelDetailsDialog.vue'

import i18n, { setLocale } from '@/i18n'

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  for (const [name, value] of Object.entries({
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
    visualViewport: {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    },
  })) {
    if (!(name in globalThis)) (globalThis as Record<string, unknown>)[name] = value
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})
beforeEach(() => setLocale('zh-CN'))

// i18n-data: a role name the project chose, rendered as its user data
const ROLES = [
  { key: 'admin', name: '管理员' },
  { key: 'member', name: '成员' },
  { key: 'r-tester', name: '内测用户' },
]

function mount(extra: Record<string, unknown> = {}) {
  const topic = {
    id: 't1',
    project_id: 'p1',
    title: '更新',
    kind: 'topic',
    status: 'active',
    joined: true,
    created_at: '',
    ...extra,
  } as unknown as Topic
  return render(ChannelDetailsDialog, {
    props: {
      modelValue: true,
      topic,
      canManage: true,
      managesProject: true,
      managerName: null,
      level: 'mentions',
      mutedUntil: null,
      roles: ROLES,
    },
    global: { plugins: [vuetify, i18n] },
  })
}

/** Vuetify's radios and switches read the input's own `input` event. */
async function tick(input: HTMLElement, checked: boolean) {
  ;(input as HTMLInputElement).checked = checked
  await fireEvent.input(input)
}

async function settingsTab() {
  const dialog = await screen.findByRole('dialog')
  await fireEvent.click(within(dialog).getByRole('tab', { name: '设置' }))
  return dialog
}

describe('频道详情：谁能发言', () => {
  it('改成指定角色时先交出管理员和成员', async () => {
    const { emitted } = mount()
    const dialog = await settingsTab()
    expect(within(dialog).queryByText('允许所有人在支线里回复')).toBeNull()
    await tick(within(dialog).getByLabelText('指定角色'), true)
    expect(emitted('speakers')).toEqual([[['admin', 'member'], true]])
  })

  it('勾掉最后一个角色不交出去', async () => {
    const { emitted } = mount({ speaker_roles: ['member'] })
    const dialog = await settingsTab()
    await fireEvent.click(within(dialog).getByText('成员'))
    expect(emitted('speakers')).toBeUndefined()
    await fireEvent.click(within(dialog).getByText('内测用户'))
    expect(emitted('speakers')).toEqual([[['member', 'r-tester'], true]])
  })

  it('支线开关只在限了角色时出现，拨它时名单原样带上', async () => {
    const { emitted } = mount({ speaker_roles: ['member'] })
    const dialog = await settingsTab()
    await tick(within(dialog).getByLabelText('允许所有人在支线里回复'), false)
    expect(emitted('speakers')).toEqual([[['member'], false]])
  })
})
