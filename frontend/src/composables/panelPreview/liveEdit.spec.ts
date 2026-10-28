/**
 * 在预览格里和几个人一起改一份 markdown：这一层管「哪一份开着」和「怎么打开」。
 *
 * 规则：能同编的只有这个房间的文本 markdown；点「编辑」立刻上屏（不等一次请求），要票
 * 和连接交给协作会话——它开不成时把理由摆出来、把这一份收起来。同一份开着时再点一次
 * 不重开；屏幕上换了另一份文件（或换了房间）就收起来。存回一版要让这一页重读。
 */
import { createApp, defineComponent, nextTick, reactive } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { usePanelPreviewLiveEdit } from './liveEdit'
import { createPreviewState } from './previewState'

// 协作会话是取数那一半的事，各自有用例。这里换成一只替身：它记得 `open` 被怎么问过，
// 能按用例说的回一张票或抛错，也能把「存回了几版」「被删了」摆出来。refs 在工厂里建一次
// 并留在 `collab` 上：用例改的是同一批 ref，这一层 watch 的也是它们。
const collab = vi.hoisted(() => ({
  refs: null as null | Record<string, { value: unknown }>,
  source: null as null | { watch: () => string | null; open: (key: string) => Promise<unknown> },
}))

vi.mock('../useDocCollab', async () => {
  const { ref } = await import('vue')
  return {
    useDocCollab: (_document: unknown, source: never) => {
      collab.source = source
      const refs = {
        session: ref(null),
        connection: ref('connecting'),
        synced: ref(false),
        readOnly: ref(false),
        peers: ref([]),
        error: ref(null),
        outdated: ref(false),
        stores: ref(0),
        deleted: ref(false),
      }
      collab.refs = refs as unknown as Record<string, { value: unknown }>
      return refs
    },
  }
})

const api = vi.hoisted(() => ({ openRoomFileLive: vi.fn() }))
vi.mock('@/api/roomFileCollab', () => api)

const apps: ReturnType<typeof createApp>[] = []

beforeEach(() => {
  vi.resetAllMocks()
  collab.refs = null
  collab.source = null
  api.openRoomFileLive.mockResolvedValue({
    enabled: true,
    document: 'roomfile:abc',
    ticket: 'tok',
    read_only: false,
    version: 'v1',
    content: '# 一\n',
  })
})

afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

function setup(path = 'notes/记录.md', over: Record<string, unknown> = {}) {
  const state = createPreviewState()
  state.previewFile.value = {
    path,
    content: '# 一\n',
    version: 'v1',
    bytes: 6,
    binary: false,
    too_large: false,
    ...over,
  } as never
  const props = reactive({ topicId: 'room', projectId: 'project' })
  const refreshDocument = vi.fn()
  let bundle!: ReturnType<typeof usePanelPreviewLiveEdit>
  const app = createApp(
    defineComponent({
      setup() {
        bundle = usePanelPreviewLiveEdit({ props: props as never, state, refreshDocument })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return { bundle, refreshDocument, state, props }
}

it('markdown 房间文件能同编；资料库里的原件和别的类型不能', () => {
  expect(setup('notes/记录.md').bundle.canEdit.value).toBe(true)
  expect(setup('library/原件.md').bundle.canEdit.value).toBe(false)
  expect(setup('uploads/附件.md').bundle.canEdit.value).toBe(false)
  expect(setup('notes/图.png').bundle.canEdit.value).toBe(false)
  // 二进制的正文是 null：读不出来也写不回去。
  expect(setup('notes/记录.md', { content: null }).bundle.canEdit.value).toBe(false)
})

it('点「编辑」立刻钉住这一份，编辑器上屏，不用等要票那一次请求', async () => {
  const { bundle } = setup()
  bundle.begin()
  // 还没 await 任何东西：路径已经在了（横条和编辑器这一下就能上屏）。
  expect(bundle.editingPath.value).toBe('notes/记录.md')
  expect(api.openRoomFileLive).not.toHaveBeenCalled()
  await nextTick()
  await collab.source!.open('room\u0000notes/记录.md')
  expect(api.openRoomFileLive).toHaveBeenCalledWith('room', 'notes/记录.md')
})

it('同一份开着时再点一次不重开', () => {
  const { bundle } = setup()
  bundle.begin()
  bundle.begin()
  expect(bundle.editingPath.value).toBe('notes/记录.md')
})

it('打开被拒时把理由摆出来，并把这一份收起来', async () => {
  api.openRoomFileLive.mockResolvedValue({ enabled: false, reason: 'too_large' })
  const { bundle } = setup()
  bundle.begin()
  await nextTick()
  await expect(collab.source!.open('room\u0000notes/记录.md')).rejects.toThrow()
  // 收起来了：一条连不上的会话不留在屏幕上。
  expect(bundle.editingPath.value).toBe('')
  // 理由在屏幕那一句里说得出（中文界面上的那一行字）。
  expect(bundle.refused.value).not.toBe('')
  expect(bundle.error.value).toBe(bundle.refused.value)
})

it('那一次拒绝晚到、读者已经开了另一份：不动后来的那一份，也不把理由摆到它头上', async () => {
  // 第一次要票挂住（读者等的就是它），第二次换一份文件才放它回来。
  let release!: (v: unknown) => void
  api.openRoomFileLive.mockImplementationOnce(() => new Promise((r) => (release = r)))
  api.openRoomFileLive.mockResolvedValue({
    enabled: true,
    document: 'roomfile:two',
    ticket: 'tok',
    read_only: false,
    version: 'v1',
    content: '',
  })
  const { bundle, state } = setup()
  bundle.begin()
  await nextTick()
  const stale = collab.source!.open('room\u0000notes/记录.md')

  // 换到另一份、开了它：编辑器现在说的是 B。
  state.previewFile.value = { path: 'notes/别的.md', content: '', version: 'v1' } as never
  await nextTick()
  bundle.begin()
  await nextTick()
  expect(bundle.editingPath.value).toBe('notes/别的.md')

  // A 的拒绝这会儿才回来：B 不该被收掉，屏幕上也不该出现 A 的理由。
  release({ enabled: false, reason: 'too_large' })
  await expect(stale).rejects.toThrow()
  expect(bundle.editingPath.value).toBe('notes/别的.md')
  expect(bundle.refused.value).toBe('')
})

it('屏幕上换了另一份文件就收起来，换成别的房间也一样', async () => {
  const { bundle, state, props } = setup()
  bundle.begin()
  state.previewFile.value = { path: 'notes/别的.md', content: '', version: 'v1' } as never
  await nextTick()
  expect(bundle.editingPath.value).toBe('')

  bundle.begin()
  props.topicId = 'another-room'
  await nextTick()
  expect(bundle.editingPath.value).toBe('')
})

it('存回一版让这一页重读', async () => {
  const { bundle, refreshDocument } = setup()
  bundle.begin()
  await nextTick()
  ;(collab.refs!.stores as { value: number }).value += 1
  await nextTick()
  expect(refreshDocument).toHaveBeenCalled()
})
