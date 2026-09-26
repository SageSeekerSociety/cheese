import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const roomFileRevisions = vi.fn()
const restoreRoomFileRevision = vi.fn()
const downloadRoomFileRevision = vi.fn()

vi.mock('../../../api', () => ({
  roomFileRevisions: (...a: unknown[]) => roomFileRevisions(...a),
  restoreRoomFileRevision: (...a: unknown[]) => restoreRoomFileRevision(...a),
  downloadRoomFileRevision: (...a: unknown[]) => downloadRoomFileRevision(...a),
}))

import RoomFileHistory from './RoomFileHistory.vue'

function row(seq: number, extra: Record<string, unknown> = {}) {
  return {
    id: `r${seq}`,
    path: 'output/报告.docx',
    seq,
    version: `v${seq}`,
    size: 10,
    author: seq === 3 ? 'cheese' : 'alice',
    author_kind: seq === 3 ? 'agent' : 'human',
    source: seq === 3 ? 'ai' : 'editor',
    note: seq === 3 ? '补了结论' : null,
    editor_key: null,
    created_at: '2026-09-25T10:00:00Z',
    ...extra,
  }
}

function mount() {
  return render(RoomFileHistory, {
    props: { topicId: 'room', path: 'output/报告.docx' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  roomFileRevisions.mockResolvedValue({ data: [row(3), row(2), row(1)], total: 3, version: 'v3' })
  restoreRoomFileRevision.mockResolvedValue(row(4))
})
afterEach(cleanup)

it('lists every save with who made it and what they said changed', async () => {
  mount()
  expect(await screen.findByText('第 3 版')).toBeTruthy()
  expect(screen.getByText('补了结论')).toBeTruthy()
  expect(screen.getByText('第 1 版')).toBeTruthy()
})

it('restores an earlier save only after the reader confirms', async () => {
  const { emitted } = mount()
  await screen.findByText('第 1 版')
  const buttons = screen.getAllByText('恢复到这一版')
  await fireEvent.click(buttons[buttons.length - 1])

  await fireEvent.click(screen.getByText('取消'))
  expect(restoreRoomFileRevision).not.toHaveBeenCalled()

  await fireEvent.click(buttons[buttons.length - 1])
  await fireEvent.click(screen.getByText('恢复'))
  await waitFor(() => expect(restoreRoomFileRevision).toHaveBeenCalledWith('room', 'r1'))
  expect(emitted().restored).toBeTruthy()
})

it('offers no restore for the current save', async () => {
  mount()
  await screen.findByText('第 3 版')
  // Three rows, two restorable: the newest is what the file already is.
  expect(screen.getAllByText('恢复到这一版')).toHaveLength(2)
})
