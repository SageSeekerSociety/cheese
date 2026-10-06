/** 总览那一格的接线外壳（`PanelOverviewHost`）。
 *
 * 两头各自有主：面板那一跳在 `panels/PanelOverview.spec.ts`（点开一个任务、点开一份摆
 * 出来的东西，事件得从面板里出来），取数那一跳在 `composables/usePanelOverview.spec.ts`
 * （什么时候去拉、先画缓存）。这一份钉的是夹在中间的这只外壳：
 *   ① 取来的数有没有原样落到面板的 props 上；
 *   ② 面板发出来的事件有没有转给 WorkPanel，一个都不掉；
 *   ③ 按模板建一份之后就地开成页签 —— 建在取数那一层，开是外壳这一步；
 *   ④ `defineExpose` 透给 WorkPanel 的三只（pulse / highlightTurn / reviewEdits）有没有落到面板上。
 *
 * 挂的是真外壳：挡在 `lib/topicPanelCache` 和 `../api` 这一层，组合式函数照常跑，面板换成
 * 只会记 props、只会 emit 的桩。文档那一半（`usePanelDoc` / `useDocThreads` / `useDocPeople`）
 * 不在这条用例的范围里，换成空桩，免得把整个编辑器拖进 happy-dom。
 */
import type { Topic } from '../../cx_types'

import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listRoomTasks: vi.fn(),
  getProgress: vi.fn(),
  listRoomOutputs: vi.fn(),
  newFromTemplate: vi.fn(),
  listDocumentTemplates: vi.fn(),
  exposed: [] as string[],
}))

vi.mock('../../lib/topicPanelCache', () => ({
  cachedTopicPanel: () => undefined,
  clearTopicPanelCache: () => {},
  fetchRoomTasks: (...a: unknown[]) => mocks.listRoomTasks(...a),
  fetchTopicProgress: (...a: unknown[]) => mocks.getProgress(...a),
}))

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomOutputs: (...a: unknown[]) => mocks.listRoomOutputs(...a),
    newFromTemplate: (...a: unknown[]) => mocks.newFromTemplate(...a),
    listDocumentTemplates: (...a: unknown[]) => mocks.listDocumentTemplates(...a),
    saveRoomOutputToLibrary: vi.fn(),
  }
})

vi.mock('../../composables/usePanelDoc', () => ({ usePanelDoc: () => ({ documentId: { value: null } }) }))
vi.mock('../../composables/useDocThreads', () => ({ useDocThreads: () => ({}) }))
vi.mock('../../composables/useDocPeople', () => ({ useDocPeople: () => ({}) }))

vi.mock('../panels/PanelOverview.vue', () => ({
  default: {
    name: 'PanelOverviewStub',
    props: [
      'docPanel',
      'docThreads',
      'docPeople',
      'topic',
      'activityTick',
      'topicList',
      'progressItems',
      'boardRows',
      'boardLoading',
      'boardError',
      'outputs',
      'outputTemplates',
      'loadOutputTemplates',
      'saveOutputToLibrary',
      'createOutputFromTemplate',
    ],
    emits: ['open-topic', 'open-card', 'mention-click', 'open-file', 'open-output'],
    setup(props: Record<string, unknown>, { expose }: { expose: (v: unknown) => void }) {
      expose({
        pulse: () => mocks.exposed.push('pulse'),
        highlightTurn: (id: string) => mocks.exposed.push(`highlight:${id}`),
        reviewEdits: (r: { title: string }) => mocks.exposed.push(`review:${r.title}`),
      })
      return { props }
    },
    template: `<div>
      <span class="board">{{ (props.boardRows || []).map((r) => r.title).join(',') }}</span>
      <span class="progress">{{ (props.progressItems || []).map((i) => i.subject).join(',') }}</span>
      <span class="outputs">{{ (props.outputs || []).map((o) => o.path).join(',') }}</span>
      <button type="button" class="card" @click="$emit('open-card', 'task-1')">卡</button>
      <button type="button" class="output" @click="$emit('open-output', 'out/a.docx')">产物</button>
      <button type="button" class="mention" @click="$emit('mention-click', 'u1')">人</button>
      <button type="button" class="make" @click="props.createOutputFromTemplate('weekly', '文档/周报.docx')">建</button>
    </div>`,
  },
}))

import PanelOverviewHost from './PanelOverviewHost.vue'

import { setLocale } from '@/i18n'

const ROOM = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
} as Topic

beforeEach(() => {
  setLocale('zh-CN')
  mocks.exposed = []
  mocks.listRoomTasks.mockReset().mockResolvedValue({
    data: [{ id: 't1', title: '分页接口' }],
    total: 1,
  })
  mocks.getProgress.mockReset().mockResolvedValue({
    items: [{ id: '1', subject: '梳理数据模型', status: 'completed' }],
    updated_at: null,
  })
  mocks.listRoomOutputs.mockReset().mockResolvedValue({
    data: [{ path: 'out/a.docx', mime: '', kind: 'file', shown_at: '2026-09-20T10:00:00Z' }],
    total: 1,
  })
  mocks.listDocumentTemplates.mockReset().mockResolvedValue({ data: [], total: 0 })
  mocks.newFromTemplate.mockReset().mockResolvedValue({ path: '文档/周报.docx', version: 'v1' })
})

function open() {
  return render(PanelOverviewHost, { props: { topic: ROOM, active: true } })
}

describe('外壳递给面板的那一包', () => {
  it('取来的活、进度、产物原样落到面板的 props 上', async () => {
    const { container } = open()

    await waitFor(() => expect(container.querySelector('.board')!.textContent).toBe('分页接口'))
    expect(container.querySelector('.progress')!.textContent).toBe('梳理数据模型')
    expect(container.querySelector('.outputs')!.textContent).toBe('out/a.docx')
  })
})

describe('面板发出来的事件', () => {
  it('原样转给 WorkPanel', async () => {
    const { container, emitted } = open()

    await fireEvent.click(container.querySelector('.output')!)
    expect(emitted()['open-output']).toEqual([['out/a.docx']])

    await fireEvent.click(container.querySelector('.mention')!)
    expect(emitted()['mention-click']).toEqual([['u1']])

    await fireEvent.click(container.querySelector('.card')!)
    expect(emitted()['open-card']).toEqual([['task-1']])
  })
})

describe('按模板建一份', () => {
  it('建出来之后就地把新文件开成页签', async () => {
    const { container, emitted } = open()

    await fireEvent.click(container.querySelector('.make')!)

    await waitFor(() => expect(emitted()['open-output']).toEqual([['文档/周报.docx']]))
    expect(mocks.newFromTemplate).toHaveBeenCalledWith('room-1', 'weekly', '文档/周报.docx')
  })
})

/** 外壳交出去的那三只不挂在 props 上，只有实例上拿得到——和 `PreviewPages.spec.ts` 一样从元素
 *  上取。根元素是面板桩画的，元素上挂的实例也就是桩本身，宿主在它的上一层：取错一层就只会验到
 *  桩，把宿主的 `defineExpose` 掏空也照样绿。 */
type HostApi = {
  pulse: () => void
  highlightTurn: (id: string) => void
  reviewEdits: (r: { title: string }) => void
}

function exposedOf(container: Element): HostApi {
  type Want = { pulse?: () => void; highlightTurn?: (id: string) => void; reviewEdits?: (r: { title: string }) => void }
  const el = container.firstElementChild as Element & {
    __vueParentComponent?: { parent?: { exposed?: Want } }
  }
  const exposed = el?.__vueParentComponent?.parent?.exposed
  if (!exposed?.pulse) throw new Error('PanelOverviewHost 没把 pulse / highlightTurn / reviewEdits 交出来')
  return exposed as HostApi
}

describe('透给 WorkPanel 的那三只', () => {
  it('都落到面板上', async () => {
    const { container } = open()
    const host = exposedOf(container)

    host.pulse()
    host.highlightTurn('turn-9')
    host.reviewEdits({ title: '改动' })

    expect(mocks.exposed).toEqual(['pulse', 'highlight:turn-9', 'review:改动'])
  })
})
