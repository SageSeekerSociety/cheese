// 「浏览频道」：只列我看得到的频道；没加入的能加入，加入了的能退出，综合两样都没有；
// 已归档的只在「已归档」里；新建频道要先起名，勾上私密就建成私密频道，外部成员看不到
// 新建。
import type { ChannelEntry } from '@/types/channelDirectory'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ChannelBrowseView from './ChannelBrowseView.vue'

import { setLocale, t } from '@/i18n'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

const entry = (id: string, extra: Partial<ChannelEntry> = {}): ChannelEntry => ({
  id,
  title: id,
  general: false,
  members_only: false,
  archived: false,
  joined: false,
  visible: true,
  description: null,
  member_count: 2,
  open_tasks: 0,
  last_activity_at: null,
  manager: 'alice',
  can_manage: false,
  can_administer: false,
  ...extra,
})

const channels = [
  entry('综合', { general: true, joined: true }),
  entry('前端', { joined: true }),
  entry('答疑'),
  entry('旧频道', { archived: true }),
  entry('机密', { members_only: true, visible: false, joined: false }),
]

beforeEach(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(extra: Record<string, unknown> = {}) {
  return render(ChannelBrowseView, {
    props: { channels, loading: false, error: null, busyId: null, creating: false, canCreate: true, ...extra },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function names(view: ReturnType<typeof mount>): string[] {
  return view
    .getAllByTestId('channel-browse-row')
    .map((row) => row.querySelector('[data-user-content]')?.textContent ?? '')
}

it('看不到里面的私密频道不在这里，已归档的只在「已归档」里', async () => {
  const view = mount()
  expect(names(view)).not.toContain('机密')
  expect(names(view)).not.toContain('旧频道')
  await fireEvent.click(view.getByRole('button', { name: t('work.channelBrowse.filter.archived', { count: 1 }) }))
  expect(names(view)).toEqual(['旧频道'])
})

it('没加入的能加入，加入了的能退出，综合两样都没有', async () => {
  const view = mount()
  const rows = view.getAllByTestId('channel-browse-row')
  const row = (name: string) => rows.find((r) => r.textContent?.includes(name))!
  expect(row('综合').querySelector('button.v-btn')).toBeNull()
  await fireEvent.click(row('答疑').querySelector('button.v-btn')!)
  expect(view.emitted('join')).toEqual([['答疑']])
  await fireEvent.click(row('前端').querySelector('button.v-btn')!)
  expect(view.emitted('leave')).toEqual([['前端']])
})

it('外部成员看不到「新建频道」', () => {
  const view = mount({ canCreate: false })
  expect(view.queryByRole('button', { name: t('work.projectSettings.channels.create') })).toBeNull()
})

it('没起名建不了，勾上私密建出来就是私密频道', async () => {
  const view = mount({ creatingOpen: true })
  const dialog = await vi.waitFor(() => view.getByTestId('new-channel'))
  const submit = view
    .getAllByRole('button', { name: t('work.projectSettings.channels.create') })
    .find((button) => button.closest('.v-overlay'))!
  await fireEvent.click(submit)
  expect(view.emitted('create')).toBeUndefined()

  await fireEvent.update(view.getByLabelText(t('work.projectSettings.channels.nameLabel')), '设计')
  const box = view.getByLabelText(t('work.projectSettings.channels.privateLabel')) as HTMLInputElement
  box.checked = true
  await fireEvent.input(box)
  await fireEvent.submit(dialog)
  expect(view.emitted('create')).toEqual([[{ title: '设计', description: '', membersOnly: true }]])
})
