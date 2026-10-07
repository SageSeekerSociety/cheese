// 项目设置里的「频道」：新建频道先起名，连点只建一个，建好就打开它；综合不能归档；
// 只有管这个频道的人看得到归档；没加入的频道在这里加入。私密频道：建的时候勾上，
// 名字前面是锁；管频道的人能设为私密，设回公开只给项目管理员，两边都先确认。
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale, t } from '@/i18n'

const topic = (id: string, extra: Partial<Topic> = {}): Topic =>
  ({ id, project_id: 'p', parent_id: null, title: id, kind: 'topic', status: 'active', ...extra }) as Topic

const store = vi.hoisted(() => ({
  topics: [] as Topic[],
  create: vi.fn(),
  renameTopic: vi.fn(),
  archive: vi.fn(),
  unarchive: vi.fn(),
  setJoined: vi.fn(),
  describe: vi.fn(),
  setMembersOnly: vi.fn(),
}))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import ProjectChannelSettings from './ProjectChannelSettings.vue'

beforeEach(() => {
  // 确认弹窗是一个 VOverlay，而 happy-dom 没有 visualViewport：不补上，弹窗挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
  store.topics = [
    topic('root', { kind: 'root', title: '综合', joined: true }),
    topic('design', { can_manage: true, joined: true }),
    topic('ops', { can_manage: false, joined: false }),
  ]
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function mount(props: { canMakePublic?: boolean } = {}) {
  return render(ProjectChannelSettings, {
    props: { projectId: 'p', ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('没起名时建不了频道', async () => {
  const view = mount()
  const submit = view.getByRole('button', { name: t('work.projectSettings.channels.create') })
  expect((submit as HTMLButtonElement).disabled).toBe(true)
  await fireEvent.click(submit)
  expect(store.create).not.toHaveBeenCalled()
})

it('连点只建一个，建好后打开它', async () => {
  let resolve!: (value: Topic) => void
  store.create.mockReturnValueOnce(new Promise((yes) => (resolve = yes)))
  const view = mount()
  await fireEvent.update(view.getByLabelText(t('work.projectSettings.channels.nameLabel')), '设计')
  const form = view.getByTestId('channel-new')
  await fireEvent.submit(form)
  await fireEvent.submit(form)
  expect(store.create).toHaveBeenCalledTimes(1)
  expect(store.create).toHaveBeenCalledWith('设计', '', false)
  resolve(topic('new'))
  await vi.waitFor(() =>
    expect((view.emitted('open-channel') as unknown[][] | undefined)?.[0]?.[0]).toMatchObject({ id: 'new' })
  )
})

it('综合不能归档，管着的频道可以，不管的频道没有归档', async () => {
  const view = mount()
  const archive = view.getAllByRole('button', { name: t('work.projectSettings.channels.archive') })
  expect(archive).toHaveLength(1)
  await fireEvent.click(archive[0])
  expect(store.archive).toHaveBeenCalledWith('design')
})

it('没加入的频道在这里加入', async () => {
  const view = mount()
  await fireEvent.click(view.getByRole('button', { name: t('work.channel.join') }))
  expect(store.setJoined).toHaveBeenCalledWith('ops', true)
})

it('勾上私密，建出来的就是私密频道', async () => {
  store.create.mockResolvedValueOnce(topic('secret', { members_only: true }))
  const view = mount()
  await fireEvent.update(view.getByLabelText(t('work.projectSettings.channels.nameLabel')), '机密')
  // 勾上它：Vuetify 的复选框读 input 事件，happy-dom 点一下不发这个事件。
  const box = view.getByLabelText(t('work.projectSettings.channels.privateLabel')) as HTMLInputElement
  box.checked = true
  await fireEvent.input(box)
  await fireEvent.submit(view.getByTestId('channel-new'))
  expect(store.create).toHaveBeenCalledWith('机密', '', true)
})

it('私密频道名字前面是一把锁', () => {
  store.topics.push(topic('secret', { members_only: true, joined: true }))
  const view = mount()
  const row = view.container.querySelector('[data-channel="secret"]')
  expect(row?.querySelector('.mdi-lock-outline')).not.toBeNull()
  expect(view.container.querySelector('[data-channel="design"] .mdi-lock-outline')).toBeNull()
})

it('管频道的人确认之后才把它设为私密', async () => {
  store.setMembersOnly.mockResolvedValueOnce(true)
  const view = mount()
  const make = view.getAllByRole('button', { name: t('work.projectSettings.channels.makePrivate') })
  // 只有管着的那一个频道有这一项。
  expect(make).toHaveLength(1)
  await fireEvent.click(make[0])
  expect(store.setMembersOnly).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(view.getByText(t('work.projectSettings.channels.makePrivateBody'))).toBeTruthy())
  const confirm = view
    .getAllByRole('button', { name: t('work.projectSettings.channels.makePrivate') })
    .find((button) => button.closest('.v-overlay'))
  await fireEvent.click(confirm!)
  expect(store.setMembersOnly).toHaveBeenCalledWith('design', true)
})

it('设回公开只给项目管理员', () => {
  store.topics.push(topic('secret', { members_only: true, joined: true, can_manage: true }))
  const plain = mount()
  expect(plain.queryByRole('button', { name: t('work.projectSettings.channels.makePublic') })).toBeNull()
  cleanup()
  const manager = mount({ canMakePublic: true })
  expect(manager.getByRole('button', { name: t('work.projectSettings.channels.makePublic') })).toBeTruthy()
})
