// 手机上话题列表顶上那一行：点下去是这个项目的看板。数字和看板同源——已完成、
// 已归档房间里没走完的活都不算进去。
import type { Component } from 'vue'

import { reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const listProjectTasks = vi.fn()
vi.mock('@/api', () => ({ listProjectTasks: (id: string) => listProjectTasks(id) }))

const store = reactive({ topics: [] as { id: string; status: string }[] })
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import BoardSummary from './BoardSummary.vue'

const Summary = BoardSummary as unknown as Component

function task(id: string, room_id: string, column: string) {
  return { id, room_id, presentation: { column, display_status: '' } }
}

function mount() {
  return render(Summary, {
    props: { projectId: 'p1' },
    global: { plugins: [createVuetify({ components })] },
  })
}

beforeEach(() => {
  push.mockReset()
  listProjectTasks.mockReset()
  store.topics = []
})

describe('BoardSummary', () => {
  it('点下去打开这个项目的看板', async () => {
    listProjectTasks.mockResolvedValue({ data: [] })
    mount()
    await fireEvent.click(screen.getByRole('button'))
    expect(push).toHaveBeenCalledWith({ name: 'workspace-running', params: { projectId: 'p1' } })
  })

  it('读的是这个项目的活，已归档房间里没走完的不算', async () => {
    store.topics = [{ id: 'gone', status: 'archived' }]
    listProjectTasks.mockResolvedValue({
      data: [task('a', 'r', 'needs_you'), task('b', 'gone', 'needs_you'), task('c', 'r', 'done')],
    })
    mount()
    expect(listProjectTasks).toHaveBeenCalledWith('p1')
    await waitFor(() => expect(screen.getByRole('button').textContent).toMatch(/1/))
    expect(screen.getByRole('button').textContent).not.toMatch(/2/)
  })

  it('读失败时这一行还在，还能点进看板', async () => {
    listProjectTasks.mockRejectedValue(new Error('offline'))
    mount()
    await Promise.resolve()
    await fireEvent.click(screen.getByRole('button'))
    expect(push).toHaveBeenCalledTimes(1)
  })
})
