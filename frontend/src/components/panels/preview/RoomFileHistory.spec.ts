import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useRoomFileHistory } from '../../../composables/useRoomFileHistory'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

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

// 这一只只画：取数那一包由宿主调（产品里是 `components/work/PanelPreviewHost.vue`），
// 所以这里也搭一个最小的宿主，取数照旧走被 mock 掉的接口层。
function mount() {
  const restored = vi.fn()
  const Host = defineComponent({
    setup() {
      const fileHistory = useRoomFileHistory(
        { topicId: () => 'room', path: () => 'output/报告.docx' },
        { onRestored: restored }
      )
      return () => h(RoomFileHistory, { fileHistory, projectId: 'project' })
    },
  })
  const ui = render(Host, { global: { plugins: [createVuetify({ components, directives })] } })
  return { ...ui, restored }
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
  const { restored } = mount()
  await screen.findByText('第 1 版')
  const buttons = screen.getAllByText('恢复到这一版')
  await fireEvent.click(buttons[buttons.length - 1])

  await fireEvent.click(screen.getByText('取消'))
  expect(restoreRoomFileRevision).not.toHaveBeenCalled()

  await fireEvent.click(buttons[buttons.length - 1])
  await fireEvent.click(screen.getByText('恢复'))
  await waitFor(() => expect(restoreRoomFileRevision).toHaveBeenCalledWith('room', 'r1'))
  // 恢复了一版这件事是取数那一层告诉宿主的，不在这一只身上发事件。
  await waitFor(() => expect(restored).toHaveBeenCalled())
})

it('keeps the confirmation open when the restore fails', async () => {
  restoreRoomFileRevision.mockRejectedValue(new Error('boom'))
  mount()
  await screen.findByText('第 1 版')
  const buttons = screen.getAllByText('恢复到这一版')
  await fireEvent.click(buttons[buttons.length - 1])
  await fireEvent.click(screen.getByText('恢复'))
  await waitFor(() => expect(restoreRoomFileRevision).toHaveBeenCalledWith('room', 'r1'))
  // 取数那一层把失败咽下去、只回一句 false，所以「恢复」成没成由它说了算：
  // 没成，确认框就得留着，读者能直接再点一次。
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.getByText('恢复')).toBeTruthy()
})

it('offers no restore for the current save', async () => {
  mount()
  await screen.findByText('第 3 版')
  // Three rows, two restorable: the newest is what the file already is.
  expect(screen.getAllByText('恢复到这一版')).toHaveLength(2)
})
