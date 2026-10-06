/** 总览那一格的取数：看板的活、上一轮的进度清单、「这个房间里的东西」。
 *
 * 这一份钉的是**什么时候去拉**，以及先画缓存这件事——三块的重取时机是从原来分散在
 * 三个组件里的写法照搬过来的（`active` / `refreshTick` / 换房间），搬错了不会有人
 * 看见，只会变成多打或少打的请求。画成什么样在各自的组件用例里。
 */
import type { RoomTask, Topic } from '../cx_types'

import { effectScope, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getProgress: vi.fn(),
  listRoomTasks: vi.fn(),
  listRoomOutputs: vi.fn(),
  saveRoomOutputToLibrary: vi.fn(),
  listDocumentTemplates: vi.fn(),
  newFromTemplate: vi.fn(),
}))

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, ...mocks }
})

import { usePanelOverview } from './usePanelOverview'

import { setLocale } from '@/i18n'
import { clearTopicPanelCache } from '@/lib/topicPanelCache'

const ROOM: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
}

function task(id: string): RoomTask {
  return {
    id,
    project_id: 'p1',
    room_id: 'room-1',
    title: `活 ${id}`,
    status: 'open',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', phrase: 'running' },
  }
}

const output = (path: string) => ({ path, mime: '', kind: 'file' as const, shown_at: '2026-09-20T10:00:00Z' })

function inScope<T>(fn: () => T): { value: T; stop: () => void } {
  const scope = effectScope()
  const value = scope.run(fn) as T
  return { value, stop: () => scope.stop() }
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function setup(over: { topic?: Topic | null; active?: boolean; refreshTick?: number } = {}) {
  const props = reactive({
    topic: over.topic === undefined ? ROOM : over.topic,
    active: over.active ?? true,
    refreshTick: over.refreshTick ?? 0,
  })
  const { value, stop } = inScope(() => usePanelOverview(props))
  return { panel: value, props, stop }
}

beforeEach(() => {
  clearTopicPanelCache()
  setLocale('zh-CN')
  mocks.getProgress.mockReset().mockResolvedValue({ items: [], updated_at: null })
  mocks.listRoomTasks.mockReset().mockResolvedValue({ data: [task('t1')], total: 1 })
  mocks.listRoomOutputs.mockReset().mockResolvedValue({ data: [output('out/a.docx')], total: 1 })
  mocks.saveRoomOutputToLibrary.mockReset().mockResolvedValue({ name: 'a.docx' })
  mocks.listDocumentTemplates.mockReset().mockResolvedValue({ data: [], total: 0 })
  mocks.newFromTemplate.mockReset().mockResolvedValue({ path: '文档/周报.docx', version: 'v1' })
})

describe('看板', () => {
  it('这一格不在屏幕上就不拉：切过去才拉', async () => {
    const { panel, props, stop } = setup({ active: false })
    await flush()
    expect(mocks.listRoomTasks).not.toHaveBeenCalled()

    props.active = true
    await flush()
    expect(mocks.listRoomTasks).toHaveBeenCalledTimes(1)
    expect(panel.boardRows.value.map((r) => r.id)).toEqual(['t1'])
    stop()
  })

  it('一轮结束时重拉一次', async () => {
    const { props, stop } = setup()
    await flush()
    expect(mocks.listRoomTasks).toHaveBeenCalledTimes(1)

    props.refreshTick += 1
    await flush()
    expect(mocks.listRoomTasks).toHaveBeenCalledTimes(2)
    stop()
  })

  it('切回来过的房间先画缓存里那份，再去取新的', async () => {
    const first = setup()
    await flush()
    expect(first.panel.boardRows.value.map((r) => r.id)).toEqual(['t1'])
    first.stop()

    // 这一次的请求一直不回：屏幕上该已经有上一次那份。
    mocks.listRoomTasks.mockReturnValue(new Promise(() => {}))
    const again = setup()
    expect(again.panel.boardRows.value.map((r) => r.id)).toEqual(['t1'])
    again.stop()
  })

  it('拉不到时说的是给人读的那句话', async () => {
    mocks.listRoomTasks.mockRejectedValue(new Error('boom'))
    const { panel, stop } = setup()
    await flush()
    expect(panel.boardError.value).toBe('任务列表加载失败')
    expect(panel.boardLoading.value).toBe(false)
    stop()
  })

  it('没有房间就不拉，也不留上一间的行', async () => {
    const { panel, stop } = setup({ topic: null })
    await flush()
    expect(mocks.listRoomTasks).not.toHaveBeenCalled()
    expect(panel.boardRows.value).toEqual([])
    stop()
  })
})

describe('上一轮的进度', () => {
  it('进房间取一次，每轮结束再取一次', async () => {
    mocks.getProgress.mockResolvedValue({
      items: [{ id: '1', subject: '梳理数据模型', status: 'completed' }],
      updated_at: '2026-09-24T00:00:00Z',
    })
    const { panel, props, stop } = setup()
    await flush()
    expect(panel.progressItems.value.map((i) => i.subject)).toEqual(['梳理数据模型'])

    props.refreshTick += 1
    await flush()
    expect(mocks.getProgress).toHaveBeenCalledTimes(2)
    stop()
  })

  it('拿不到就是空的，不报错 —— 进度是背景信息', async () => {
    mocks.getProgress.mockRejectedValue(new Error('boom'))
    const { panel, stop } = setup()
    await flush()
    expect(panel.progressItems.value).toEqual([])
    stop()
  })
})

describe('这个房间里的东西', () => {
  it('进房间取一次；换房间、每轮结束各再取一次', async () => {
    const { panel, props, stop } = setup()
    await flush()
    expect(panel.outputItems.value.map((o) => o.path)).toEqual(['out/a.docx'])

    props.refreshTick += 1
    await flush()
    expect(mocks.listRoomOutputs).toHaveBeenCalledTimes(2)

    props.topic = { ...ROOM, id: 'room-2' }
    await flush()
    expect(mocks.listRoomOutputs).toHaveBeenCalledTimes(3)
    expect(mocks.listRoomOutputs).toHaveBeenLastCalledWith('room-2')
    stop()
  })

  it('读不到就是空列表，不显示 —— 这一格的主体是上面的文档', async () => {
    mocks.listRoomOutputs.mockRejectedValue(new Error('boom'))
    const { panel, stop } = setup()
    await flush()
    expect(panel.outputItems.value).toEqual([])
    stop()
  })

  it('手上已经有模板就不再问第二次', async () => {
    mocks.listDocumentTemplates.mockResolvedValue({
      data: [{ id: 'weekly', name: '周报', suffix: 'docx', about: '' }],
      total: 1,
    })
    const { panel, stop } = setup()
    await flush()
    panel.loadOutputTemplates()
    await flush()
    expect(panel.outputTemplates.value.map((t) => t.name)).toEqual(['周报'])

    panel.loadOutputTemplates()
    await flush()
    expect(mocks.listDocumentTemplates).toHaveBeenCalledTimes(1)
    stop()
  })

  it('存进资料库交回它在那边叫什么', async () => {
    const { panel, stop } = setup()
    await flush()
    await expect(panel.saveOutputToLibrary('out/a.docx')).resolves.toBe('a.docx')
    expect(mocks.saveRoomOutputToLibrary).toHaveBeenCalledWith('room-1', 'out/a.docx')
    stop()
  })

  it('从模板新建：建完就地重读列表，把新文件的路径交回去', async () => {
    const { panel, stop } = setup()
    await flush()
    expect(mocks.listRoomOutputs).toHaveBeenCalledTimes(1)

    await expect(panel.createOutputFromTemplate('weekly', '文档/周报.docx')).resolves.toBe('文档/周报.docx')
    expect(mocks.newFromTemplate).toHaveBeenCalledWith('room-1', 'weekly', '文档/周报.docx')
    expect(mocks.listRoomOutputs).toHaveBeenCalledTimes(2)
    stop()
  })
})
