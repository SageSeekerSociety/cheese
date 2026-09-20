/**
 * 这个房间里的东西。
 *
 * 摆出来的东西属于这个房间，所以这一块只回答两件事：这个房间里都有什么，以及把其
 * 中一份存进项目那个动作。跑着的应用不在里面 —— 它是一个进程，没有文件可存。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomOutputs from './RoomOutputs.vue'

vi.mock('@/api', () => ({
  listRoomOutputs: vi.fn(),
  saveRoomOutputToProject: vi.fn(),
}))

const { listRoomOutputs, saveRoomOutputToProject } = await import('@/api')

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

const REPORT = {
  path: 'out/评审简报.docx',
  mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  kind: 'file' as const,
  shown_at: '2026-09-20T10:00:00Z',
}
const APP = {
  path: 'Vue dev server',
  mime: 'application/x-cheese-app',
  kind: 'app' as const,
  shown_at: '2026-09-20T09:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listRoomOutputs).mockResolvedValue({ data: [REPORT, APP], total: 2 })
  vi.mocked(saveRoomOutputToProject).mockResolvedValue({
    artifact: { id: 'a1', name: '评审简报.docx' },
    version: 1,
    path: '评审简报.docx',
  })
})

function mount() {
  return render(RoomOutputs, { props: { topicId: 't1' }, global: { plugins: [vuetify] } })
}

describe('这个房间里的东西', () => {
  it('列出摆出来过的文件，跑着的应用不在里面', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))
    expect(container.textContent).not.toContain('Vue dev server')
  })

  it('房间里什么都没摆出来时这一块不出现', async () => {
    vi.mocked(listRoomOutputs).mockResolvedValue({ data: [], total: 0 })

    const { container } = mount()

    await waitFor(() => expect(listRoomOutputs).toHaveBeenCalled())
    expect(container.querySelector('[data-testid="room-outputs"]')).toBeNull()
  })

  it('保存到项目之后说清它成了哪一项的第几版', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到项目')
    await fireEvent.click(save!)

    await waitFor(() => expect(saveRoomOutputToProject).toHaveBeenCalledWith('t1', 'out/评审简报.docx'))
    await waitFor(() => expect(container.textContent).toContain('已保存为《评审简报.docx》第 1 版'))
  })

  it('存不进去时说出来，而不是装作按过了', async () => {
    vi.mocked(saveRoomOutputToProject).mockRejectedValue(new Error('你不是这个项目的成员'))

    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('评审简报.docx'))

    const save = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存到项目')
    await fireEvent.click(save!)

    await waitFor(() => expect(container.textContent).toContain('你不是这个项目的成员'))
  })
})
