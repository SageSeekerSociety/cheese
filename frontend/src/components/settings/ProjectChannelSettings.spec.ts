// 项目设置里的「频道」：新建频道先起名，连点只建一个，建好就打开它；综合不能归档。
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
}))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import ProjectChannelSettings from './ProjectChannelSettings.vue'

beforeEach(() => {
  setLocale('zh-CN')
  store.topics = [topic('root', { kind: 'root', title: '综合' }), topic('design')]
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mount() {
  return render(ProjectChannelSettings, {
    props: { projectId: 'p' },
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
  expect(store.create).toHaveBeenCalledWith('设计')
  resolve(topic('new'))
  await vi.waitFor(() => expect(view.emitted('open-channel')?.[0]?.[0]).toMatchObject({ id: 'new' }))
})

it('综合不能归档，别的频道可以', async () => {
  const view = mount()
  const archive = view.getAllByRole('button', { name: t('work.projectSettings.channels.archive') })
  expect(archive).toHaveLength(1)
  await fireEvent.click(archive[0])
  expect(store.archive).toHaveBeenCalledWith('design')
})
