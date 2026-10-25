/**
 * 在预览格里直接改一份 markdown，存回那一份房间文件本身。
 *
 * 规则：能改的只有这个房间的文本文件（空文件也算）；存的时候带的是**打开时读到的那一
 * 版**，别人先改过就报冲突、谁的都不丢；没改过的存一次不写一版历史。
 */
import { createApp, defineComponent, nextTick } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createPreviewState } from './previewState'
import { usePanelPreviewTextEdit } from './textEdit'

import { ApiError } from '@/api'

const api = vi.hoisted(() => ({ writeRoomTextFile: vi.fn(), readPreviewFile: vi.fn() }))
vi.mock('@/api/roomFiles', () => api)
// 真模块照旧（`ApiError` 用的是它自己的那个类），只把重读那一只换成替身。
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  readPreviewFile: (...args: unknown[]) => api.readPreviewFile(...args),
}))

const apps: ReturnType<typeof createApp>[] = []

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  vi.resetAllMocks()
  api.writeRoomTextFile.mockResolvedValue({ path: 'notes/记录.md', version: 'v2', seq: 2 })
})

afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

/** 一份摆进预览格里的文件，和它那个编辑会话。 */
function setup(path: string, over: Record<string, unknown> = {}) {
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
  const refreshDocument = vi.fn()
  let bundle!: ReturnType<typeof usePanelPreviewTextEdit>
  const app = createApp(
    defineComponent({
      setup() {
        bundle = usePanelPreviewTextEdit({
          props: { topicId: 'room', projectId: 'project' } as never,
          state,
          refreshDocument,
        })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return { bundle, refreshDocument, state }
}

it('markdown 房间文件：能改，存下去带的是读到的那个版本', async () => {
  const { bundle, refreshDocument } = setup('notes/记录.md')

  expect(bundle.canEdit.value).toBe(true)
  bundle.begin()
  expect(bundle.draft.value).toBe('# 一\n')
  bundle.setDraft('# 一\n\n补了一段。\n')

  expect(await bundle.save()).toBe(true)
  expect(api.writeRoomTextFile).toHaveBeenCalledWith('room', 'notes/记录.md', '# 一\n\n补了一段。\n', 'v1')
  expect(refreshDocument).toHaveBeenCalled()
  // 存完编辑收起来。
  expect(bundle.draft.value).toBeNull()
})

it('存下去之后这一份的正文和版本一起换成刚存下那版', async () => {
  // 只换正文、留着旧版本，评论标记那几处会按一个早已过期的版本去比：正文是新写的，
  // 版本还停在读它时那一版。
  const { bundle, state } = setup('notes/记录.md')
  bundle.begin()
  bundle.setDraft('# 新的\n')

  expect(await bundle.save()).toBe(true)
  expect(state.previewFile.value?.content).toBe('# 新的\n')
  expect(state.previewFile.value?.version).toBe('v2')
})

it('编辑器开着的时候面板把这一份换成了别人的新版：存下去的仍是打开时那一版', async () => {
  // 面板在编辑器开着时照旧重读这一份（20 秒的轮询、收工那一下、读者按的刷新），
  // `previewFile` 会被换成别人刚存下的那一版。要是照那时候的版本去存，服务端一比
  // 「对得上」就放行，我打字的时候别人写的东西就被盖掉了——这一条盯的正是这个。
  const { bundle, state } = setup('notes/记录.md')
  bundle.begin()
  bundle.setDraft('# 我改的\n')

  state.previewFile.value = {
    path: 'notes/记录.md',
    content: '# 别人写的\n',
    version: 'v9',
    bytes: 15,
    binary: false,
    too_large: false,
  } as never
  await nextTick()

  api.writeRoomTextFile.mockRejectedValue(new ApiError(409, 'refused'))
  expect(await bundle.save()).toBe(false)
  // 带的是打开时那一版，不是面板后来换上的 v9。
  expect(api.writeRoomTextFile).toHaveBeenCalledWith('room', 'notes/记录.md', '# 我改的\n', 'v1')
  expect(bundle.conflicted.value).toBe(true)
})

it('编辑器开着的时候这一格换了别的文件：编辑收起来，别把 A 的字存进 B', async () => {
  const { bundle, state } = setup('notes/记录.md')
  bundle.begin()
  bundle.setDraft('# 我改的\n')

  state.previewFile.value = {
    path: 'notes/别的.md',
    content: '# 另一份\n',
    version: 'v1',
    bytes: 10,
    binary: false,
    too_large: false,
  } as never
  await nextTick()

  expect(bundle.draft.value).toBeNull()
  expect(await bundle.save()).toBe(false)
  expect(api.writeRoomTextFile).not.toHaveBeenCalled()
})

it('空文件的正文是空串，不是 null：照样能改', async () => {
  // 「读不出版本」和「正文恰好是空的」是两回事：前者的 `content` 是 null。
  const { bundle } = setup('notes/空.md', { content: '', bytes: 0 })
  expect(bundle.canEdit.value).toBe(true)
  bundle.begin()
  expect(bundle.draft.value).toBe('')
})

it('二进制 / 太大的文件不能改：读不出正文，也就写不回去', async () => {
  const { bundle } = setup('notes/图片.png', { content: null, binary: true })
  expect(bundle.canEdit.value).toBe(false)
})

it('资料库原件和消息附件不是这个房间的文件，不能改', () => {
  expect(setup('library/某个原件.md').bundle.canEdit.value).toBe(false)
  expect(setup('uploads/截图.md').bundle.canEdit.value).toBe(false)
})

it('别人先改过：这一版不写，报冲突，编辑里的字留着', async () => {
  const { bundle, refreshDocument } = setup('notes/记录.md')
  api.writeRoomTextFile.mockRejectedValue(new ApiError(409, 'refused'))
  bundle.begin()
  bundle.setDraft('# 我改的\n')

  expect(await bundle.save()).toBe(false)
  expect(bundle.conflicted.value).toBe(true)
  expect(refreshDocument).not.toHaveBeenCalled()
  // 打过的字还在，读者能先拷走。
  expect(bundle.draft.value).toBe('# 我改的\n')
})

it('报冲突之后「载入最新的」：正文和版本都换成文件现在那一版', async () => {
  const { bundle, refreshDocument } = setup('notes/记录.md')
  api.writeRoomTextFile.mockRejectedValue(new ApiError(409, 'refused'))
  bundle.begin()
  bundle.setDraft('# 我改的\n')
  expect(await bundle.save()).toBe(false)

  api.readPreviewFile.mockResolvedValue({
    path: 'notes/记录.md',
    content: '# 别人写的\n',
    version: 'v9',
    bytes: 15,
    binary: false,
    too_large: false,
  })
  await bundle.reload()

  expect(api.readPreviewFile).toHaveBeenCalledWith('room', 'notes/记录.md')
  expect(bundle.draft.value).toBe('# 别人写的\n')
  expect(bundle.conflicted.value).toBe(false)

  // 载入之后编辑器里就是文件现在那一版：读者把改的地方补回去再存，带的是最新的版本。
  api.writeRoomTextFile.mockResolvedValue({ path: 'notes/记录.md', version: 'v10', seq: 3 })
  bundle.setDraft('# 别人写的\n\n我补的。\n')
  expect(await bundle.save()).toBe(true)
  expect(api.writeRoomTextFile).toHaveBeenLastCalledWith('room', 'notes/记录.md', '# 别人写的\n\n我补的。\n', 'v9')
  expect(refreshDocument).toHaveBeenCalled()
})

it('没改过的存一次不写一版：内容一样，白留一版历史', async () => {
  const { bundle } = setup('notes/记录.md')
  bundle.begin()

  expect(await bundle.save()).toBe(true)
  expect(api.writeRoomTextFile).not.toHaveBeenCalled()
})
