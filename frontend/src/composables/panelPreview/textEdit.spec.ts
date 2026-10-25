/**
 * 在预览格里直接改一份 markdown，存回那一份房间文件本身。
 *
 * 规则：能改的只有这个房间的文本文件；存的时候带上读到的版本，别人先改过就报冲突、
 * 谁的都不丢；没改过的存一次不写一版历史。
 */
import { createApp, defineComponent } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { usePanelPreviewTextEdit } from './textEdit'
import { createPreviewState } from './previewState'

import { ApiError } from '@/api'

const api = vi.hoisted(() => ({ writeRoomTextFile: vi.fn() }))
vi.mock('@/api/roomFiles', () => api)

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

it('二进制 / 太大的文件不能改：读不出版本，也就写不回去', async () => {
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
  // 打过的字还在，读者能改一改再存。
  expect(bundle.draft.value).toBe('# 我改的\n')
})

it('没改过的存一次不写一版：内容一样，白留一版历史', async () => {
  const { bundle } = setup('notes/记录.md')
  bundle.begin()

  expect(await bundle.save()).toBe(true)
  expect(api.writeRoomTextFile).not.toHaveBeenCalled()
})
