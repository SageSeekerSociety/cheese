/**
 * 文档条上的版本这一包：屏幕上这一份是第几版、本版比读者上次看的改了几处、两版对比、
 * 看旧版。清单来自保存历史那一份（这里给假的），每一版的字节从接口读（这里 mock）。
 */
import type { RoomFileRevision } from '../../api'

import { createApp, defineComponent, nextTick, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createPreviewState } from './previewState'
import { usePanelPreviewVersions } from './versions'

const api = vi.hoisted(() => ({ roomFileRevisionBytes: vi.fn() }))
vi.mock('../../api', () => api)

const apps: ReturnType<typeof createApp>[] = []
let bytes: Record<string, string> = {}

beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
  bytes = {}
  api.roomFileRevisionBytes.mockImplementation(async (_t: string, id: string) => {
    if (!(id in bytes)) throw new Error('missing')
    return new TextEncoder().encode(bytes[id]).buffer
  })
})
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

let n = 0
/** 一份文件的几版（新的在前），内容放进 mock 的接口里。id 每次都不同：字节按 id 缓存在模块里。 */
function revisions(path: string, contents: string[], authors: [string, 'human' | 'agent'][] = []): RoomFileRevision[] {
  n += 1
  return contents
    .map((text, i) => {
      const id = `r${n}-${i + 1}`
      bytes[id] = text
      const [author, kind] = authors[i] ?? ['cheese-1', 'agent']
      return {
        id,
        path,
        seq: i + 1,
        version: `v${n}-${i + 1}`,
        size: text.length,
        author,
        author_kind: kind,
        source: 'ai',
        note: null,
        editor_key: null,
        created_at: new Date().toISOString(),
      } as RoomFileRevision
    })
    .reverse()
}

function setup(path: string, rows: RoomFileRevision[], showing = rows[0]?.version) {
  const state = createPreviewState()
  state.previewFile.value = { path, content: '', version: showing } as never
  const history = { rows: ref(rows) }
  let bundle!: ReturnType<typeof usePanelPreviewVersions>
  const app = createApp(
    defineComponent({
      setup() {
        bundle = usePanelPreviewVersions({
          props: {
            topicId: 'room',
            projectId: 'p',
            agentName: '芝士',
            members: [{ user_handle: 'cheese-1', agent: true, name: '芝士Opus' }],
          } as never,
          state,
          fileHistory: history as never,
          people: { names: ref({ wang: '王长鑫' }) } as never,
          editing: ref(null),
        })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return { bundle, state }
}

it('屏幕上这一份按内容哈希对上清单里的那一版；作者照名册写，队友也是', () => {
  const rows = revisions(
    'a.md',
    ['一', '二'],
    [
      ['wang', 'human'],
      ['cheese-1', 'agent'],
    ]
  )
  const { bundle } = setup('a.md', rows)
  expect(bundle.current.value).toMatchObject({ seq: 2, author: '芝士Opus', latest: true })
  expect(bundle.list.value.map((e) => e.author)).toEqual(['芝士Opus', '王长鑫'])
})

it('这一份对不上清单里任何一版：不说第几版', () => {
  const { bundle } = setup('a.md', revisions('a.md', ['一', '二']), 'not-in-history')
  expect(bundle.current.value).toBeNull()
  expect(bundle.canCompare.value).toBe(false)
})

it('资料库里的原件没有版本行', () => {
  const { bundle } = setup('library/a.md', revisions('library/a.md', ['一', '二']))
  expect(bundle.list.value).toEqual([])
})

it('提示条：没记过看到哪版就和上一版比，数的是连着的改动；收起之后同一版不再提示', async () => {
  const { bundle } = setup('a.md', revisions('a.md', ['# 题\n\n甲\n\n乙\n\n丙', '# 题\n\n甲改\n\n乙\n\n丙\n\n丁']))
  await vi.waitFor(() => expect(bundle.hint.value).toEqual({ base: 1, count: 2, unit: 'place' }))
  bundle.dismissHint()
  await nextTick()
  expect(bundle.hint.value).toBeNull()
})

it('提示条拿读者上次看到的那一版比，不一定是上一版', async () => {
  const rows = revisions('b.md', ['甲', '乙', '丙'])
  localStorage.setItem('cheese.preview.seenVersion:room:b.md', '1')
  const { bundle } = setup('b.md', rows)
  await vi.waitFor(() => expect(bundle.hint.value?.base).toBe(1))
  expect(bundle.list.value.find((e) => e.seq === 1)?.lastSeen).toBe(true)
})

it('比出来 0 处（存了一份一样的）不提示', async () => {
  const { bundle } = setup('c.md', revisions('c.md', ['同样', '同样']))
  await vi.waitFor(() => expect(api.roomFileRevisionBytes).toHaveBeenCalledTimes(2))
  await nextTick()
  expect(bundle.hint.value).toBeNull()
})

it('对比：查看改动进对比态，比的是提示条那一版；退出回到正文', async () => {
  const { bundle } = setup('d.md', revisions('d.md', ['甲\n乙', '甲\n乙改']))
  bundle.openCompare()
  await vi.waitFor(() => expect(bundle.compare.value?.changes?.count).toBe(1))
  expect(bundle.compare.value).toMatchObject({ base: 1, target: 2, loading: false, error: '' })
  bundle.exitCompare()
  expect(bundle.compare.value).toBeNull()
})

it('菜单里点旧版：Markdown 画得出来就看它；点当前那一版回到正文', async () => {
  const { bundle } = setup('e.md', revisions('e.md', ['旧的正文', '新的正文']))
  bundle.choose(1)
  await vi.waitFor(() => expect(bundle.oldView.value).toEqual({ seq: 1, kind: 'markdown', text: '旧的正文' }))
  bundle.choose(2)
  expect(bundle.oldView.value).toBeNull()
})

it('表格：比出改了哪几格，提示条按格数说，格子上的标记带原值', async () => {
  const { bundle } = setup('f.csv', revisions('f.csv', ['月份,订单\n10月,150\n', '月份,订单\n10月,160\n']))
  await vi.waitFor(() => expect(bundle.hint.value).toEqual({ base: 1, count: 1, unit: 'cell' }))
  expect([...(bundle.changedCells.value ?? [])]).toEqual([['!B2', '150']])
  expect(bundle.changedBy.value).toBe('芝士Opus')
})

it('PDF 这类比不了的：没有对比，旧版能直接看', () => {
  const { bundle } = setup('g.pdf', revisions('g.pdf', ['%PDF-1', '%PDF-2']))
  expect(bundle.canCompare.value).toBe(false)
  expect(bundle.canView.value).toBe(true)
  expect(bundle.hint.value).toBeNull()
})
