// @ 了不在频道里的人，发出去之后问我要不要把他们加进来（Slack 那样）：加，走频道名册
// 本来那条加人的路；不加，谁也不进来；我加不了人，就不给那颗按钮；换了对话就不再问。
import type { PostedMessage } from '../api/messages'
import type { Block } from '../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MentionOutsidePrompt from '../components/chat/MentionOutsidePrompt.vue'

import { useMentionOutside } from './useMentionOutside'

import { t } from '@/i18n'

const addTopicMember = vi.fn(async (_room: string, handle: string) => ({ member_handle: handle }))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  addTopicMember: (room: string, handle: string) => addTopicMember(room, handle),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const NAMES: Record<string, string> = { carol: '张衡', erin: '李冰' }

function sent(outside?: PostedMessage['mentioned_outside']): PostedMessage {
  return { id: 'b1', conversation_id: 'room-1', ...(outside ? { mentioned_outside: outside } : {}) } as Block
}

// The panel's composer zone, as ChatPanel wires it.
function mount(place = ref('room-1')) {
  let api!: ReturnType<typeof useMentionOutside>
  const Host = defineComponent({
    setup() {
      api = useMentionOutside({ place: () => place.value, nameOf: (handle) => NAMES[handle] ?? handle })
      return () =>
        api.prompt.value
          ? h(MentionOutsidePrompt, {
              names: api.names(api.prompt.value.handles),
              membersOnly: api.prompt.value.members_only,
              mayAdd: api.prompt.value.may_add,
              adding: api.adding.value,
              onAdd: api.add,
              onDismiss: api.dismiss,
            })
          : null
    },
  })
  render(Host, { global: { plugins: [createVuetify()] } })
  return { api: () => api, place }
}

const addButton = () => screen.queryByRole('button', { name: t('work.room.mentionOutside.add') })
const prompt = () => screen.queryByRole('status')

describe('naming someone who is not in the channel', () => {
  beforeEach(() => addTopicMember.mockClear())

  it('asks the sender, and adding brings each of them into the channel', async () => {
    const { api } = mount()
    api().offer(sent({ room_id: 'room-1', handles: ['carol', 'erin'], may_add: true, members_only: true }))
    await vi.waitFor(() => expect(prompt()?.textContent).toContain('张衡'))
    expect(prompt()?.textContent).toContain('李冰')

    await fireEvent.click(addButton()!)

    await vi.waitFor(() => expect(prompt()).toBeNull())
    expect(addTopicMember.mock.calls).toEqual([
      ['room-1', 'carol'],
      ['room-1', 'erin'],
    ])
  })

  it('declining adds nobody', async () => {
    const { api } = mount()
    api().offer(sent({ room_id: 'room-1', handles: ['carol'], may_add: true, members_only: false }))
    await vi.waitFor(() => expect(prompt()).not.toBeNull())

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.mentionOutside.dismiss') }))

    await vi.waitFor(() => expect(prompt()).toBeNull())
    expect(addTopicMember).not.toHaveBeenCalled()
  })

  it('does not offer to add them to someone who may not', async () => {
    const { api } = mount()
    api().offer(sent({ room_id: 'room-1', handles: ['carol'], may_add: false, members_only: false }))
    await vi.waitFor(() => expect(prompt()?.textContent).toContain('张衡'))

    expect(addButton()).toBeNull()
    await api().add()
    expect(addTopicMember).not.toHaveBeenCalled()
  })

  it('keeps asking about whoever could not be added', async () => {
    addTopicMember.mockImplementationOnce(async () => {
      throw new Error('无法添加')
    })
    const { api } = mount()
    api().offer(sent({ room_id: 'room-1', handles: ['carol', 'erin'], may_add: true, members_only: true }))
    await vi.waitFor(() => expect(addButton()).not.toBeNull())

    await fireEvent.click(addButton()!)

    await vi.waitFor(() => expect(prompt()?.textContent).not.toContain('李冰'))
    expect(prompt()?.textContent).toContain('张衡')
  })

  it('asks nothing for a message that names nobody outside', async () => {
    const { api } = mount()
    api().offer(sent())
    await Promise.resolve()
    expect(prompt()).toBeNull()
  })

  it('stops asking once the sender moves to another conversation', async () => {
    const { api, place } = mount()
    api().offer(sent({ room_id: 'room-1', handles: ['carol'], may_add: true, members_only: true }))
    await vi.waitFor(() => expect(prompt()).not.toBeNull())

    place.value = 'room-2'

    await vi.waitFor(() => expect(prompt()).toBeNull())
  })
})
