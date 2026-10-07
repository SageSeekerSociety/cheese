// 项目设置里的「频道」，只给管项目的人：每个频道一行，「⋯」里管它。综合不能归档；
// 自己不在里面的私密频道只能加入、换管理者、归档，不能改名或看里面；设为私密要先
// 确认；换管理者交给页面传进来的那一个动作。
import type { ChannelEntry } from '@/types/channelDirectory'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale, t } from '@/i18n'

const store = vi.hoisted(() => ({
  renameTopic: vi.fn(),
  archive: vi.fn(),
  unarchive: vi.fn(),
  describe: vi.fn(),
  setMembersOnly: vi.fn(),
}))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import ProjectChannelSettings from './ProjectChannelSettings.vue'

const entry = (id: string, extra: Partial<ChannelEntry> = {}): ChannelEntry => ({
  id,
  title: id,
  general: false,
  members_only: false,
  archived: false,
  joined: true,
  visible: true,
  description: null,
  member_count: 3,
  open_tasks: 0,
  last_activity_at: null,
  manager: 'alice',
  can_manage: true,
  can_administer: true,
  ...extra,
})

// 菜单定位读它，而清理之后菜单还会再量一次：放在全局，不随每个用例撤掉。
Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

let channels: ChannelEntry[] = []
const load = vi.fn(async () => ({ items: channels, manages_project: true }))
const stepIn = vi.fn(async () => ({}))
const handOver = vi.fn(async () => ({}))

beforeEach(() => {
  // 菜单和确认弹窗都是 VOverlay，而 happy-dom 没有 visualViewport：不补上，挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
  channels = [
    entry('general', { general: true, manager: null, can_administer: false }),
    entry('design'),
    entry('secret', { members_only: true, joined: false, visible: false, can_manage: false, manager: 'carol' }),
  ]
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

async function mount() {
  const view = render(ProjectChannelSettings, {
    props: {
      projectId: 'p',
      members: [
        { user_handle: 'alice', name: 'Alice', agent: false },
        { user_handle: 'bob', name: 'Bob', agent: false },
      ] as never,
      load,
      stepIn,
      handOver,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await vi.waitFor(() => expect(view.getAllByTestId('channel-admin-row')).toHaveLength(3))
  return view
}

async function menuOf(view: Awaited<ReturnType<typeof mount>>, name: string): Promise<string[]> {
  await fireEvent.click(view.getByRole('button', { name: t('work.projectSettings.channels.actionsFor', { name }) }))
  await vi.waitFor(() => expect(document.querySelector('.v-overlay--active')).not.toBeNull())
  return Array.from(document.querySelectorAll('.v-overlay--active .v-list-item-title')).map((n) => n.textContent ?? '')
}

it('综合没有归档，管着的频道有', async () => {
  const view = await mount()
  expect(await menuOf(view, 'general')).not.toContain(t('work.projectSettings.channels.archive'))
  cleanup()
  expect(await menuOf(await mount(), 'design')).toContain(t('work.projectSettings.channels.archive'))
})

it('不在里面的私密频道只能加入、换管理者、归档，不能改名', async () => {
  const view = await mount()
  const items = await menuOf(view, 'secret')
  expect(items).toEqual([
    t('work.projectSettings.channels.stepIn'),
    t('work.projectSettings.channels.changeManager'),
    t('work.projectSettings.channels.archive'),
  ])
})

it('加入私密频道交给页面的那一个动作', async () => {
  const view = await mount()
  await menuOf(view, 'secret')
  await fireEvent.click(view.getByText(t('work.projectSettings.channels.stepIn')))
  await vi.waitFor(() => expect(stepIn).toHaveBeenCalledWith('p', 'secret'))
})

it('确认之后才设为私密', async () => {
  store.setMembersOnly.mockResolvedValueOnce(true)
  const view = await mount()
  await menuOf(view, 'design')
  await fireEvent.click(view.getByText(t('work.projectSettings.channels.makePrivate')))
  expect(store.setMembersOnly).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(view.getByText(t('work.projectSettings.channels.makePrivateBody'))).toBeTruthy())
  const confirm = view
    .getAllByRole('button', { name: t('work.projectSettings.channels.makePrivate') })
    .find((button) => button.closest('.v-overlay'))
  await fireEvent.click(confirm!)
  await vi.waitFor(() => expect(store.setMembersOnly).toHaveBeenCalledWith('design', true))
})
