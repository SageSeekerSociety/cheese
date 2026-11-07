/** 现场读失败时，那一格要留在原地，并给出一条能走的路。
 *
 * 从前的错误态是一条红字，没有按钮：一次超时就只能刷新整页才回得来，而刷新还会
 * 把读者翻到的位置一起丢掉。这里钉两件事——失败时画的是那句「加载失败」加一颗
 * 「重试」，以及按下它真的重新读一次。
 *
 * 「重新读一次」读的是**失败的那一次**：读者往上翻的那一页失败时，重试重发的是
 * 那一页（带 `before` 游标），不是跳回末尾读最新一页——后者在他停在历史中间、
 * 窗口又被裁过时会被守卫整个丢掉，一次请求都不发，按了等于没按。
 */
import type { Block } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('../../composables/useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
// 会话栏只当一格占位：这一条关心的是它下面那一块。
vi.mock('../SessionInspector.vue', () => ({ default: { template: '<div><slot name="actions" /></div>' } }))

import { usePanelSite } from '../../composables/usePanelSite'
import { cachedWindow, forgetWindows, rememberWindow } from '../../lib/siteWindow'

import PanelSite from './PanelSite.vue'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  forgetWindows()
  api.getTranscript.mockReset()
  api.sessionRecordImageUrl.mockReset()
  tick.value = 0
})

afterEach(cleanup)

const tick = ref(0)

function open() {
  const Host = defineComponent({
    setup() {
      const site = usePanelSite({
        topicId: () => 'room',
        active: () => true,
        refreshTick: () => tick.value,
        runningTurns: () => ({}),
        agentControl: () => null,
      })
      return () => h(PanelSite, { topicId: 'room', active: true, memberNames: {}, site })
    },
  })
  return render(Host, { global: { plugins: [vuetify] } })
}

describe('a 现场 that could not be read', () => {
  it('says so in place and reads again when the reader asks it to', async () => {
    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    const view = open()

    await waitFor(() => expect(view.container.textContent).toContain('加载失败'))
    expect(view.container.textContent).toContain('请求等待超时，请重试')

    // 重试真的又读了一次，而不是只把红字收起来；读成了这一格就回到正常样子。
    api.getTranscript.mockClear()
    api.getTranscript.mockResolvedValue({ data: [], has_more: false })
    await fireEvent.click(view.getByRole('button', { name: '重试' }))

    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).not.toContain('加载失败'))
  })
})

/** 上次打开这段现场时留下的那一窗：一行带着游标的工具调用。 */
function heldRow(): Block {
  const parts = [{ kind: 'tool', name: 'Bash', input: { command: 'echo held-row' }, preview: 'echo held-row' }]
  return {
    id: 'held',
    conversation_id: 'room',
    turn_id: 'turn',
    author: 'cheese-a',
    author_type: 'participant',
    kind: 'event',
    content: '',
    created_at: '2026-11-07T10:00:00Z',
    refs: [],
    meta: { record: { type: 'assistant', message: { content: parts } }, parts, tool: 'Bash', cursor: 'c-held' },
  } as Block
}

function holdWindow() {
  rememberWindow(
    'room',
    null,
    { blocks: [heldRow()], hasMore: false, oldestCursor: 'c-held', newestCursor: 'c-held' },
    120
  )
}

describe('a 现场 already held from last time', () => {
  // 弱网、离线时打开：缓存先画出来了，这一次读又没成。那几行还是好的，报错页不能
  // 把它们挡住；之后的刷新从缓存的游标往后接着读，网回来就自己追上。
  it('keeps the held window on screen when the read fails, and picks up from it once the network is back', async () => {
    holdWindow()
    api.getTranscript.mockRejectedValue(new Error('offline'))
    const view = open()

    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).toContain('echo held-row'))
    expect(view.container.textContent).not.toContain('加载失败')
    expect(view.container.textContent).not.toContain('offline')

    // 网回来了：下一次刷新只问缓存那一条之后的，新的一行接在后面，旧的那行还在。
    const parts = [{ kind: 'tool', name: 'Bash', input: { command: 'echo new-row' }, preview: 'echo new-row' }]
    const fresh = {
      ...heldRow(),
      id: 'fresh',
      created_at: '2026-11-07T10:00:05Z',
      meta: { ...heldRow().meta, parts, cursor: 'c-fresh' },
    } as Block
    api.getTranscript.mockReset()
    api.getTranscript.mockResolvedValue({ data: [fresh], has_more: false, newest_id: 'c-fresh' })
    tick.value++

    await waitFor(() => expect(view.container.textContent).toContain('echo new-row'))
    expect(api.getTranscript).toHaveBeenCalledWith('room', expect.objectContaining({ after: 'c-held' }))
    expect(view.container.textContent).toContain('echo held-row')
  })

  // 权限被拒是另一回事：这段现场不给看了，缓存那几行一行也不能留在屏上，下次打开
  // 也不能再先画出来。
  it('takes the held window down and says it is not yours to see when the read is refused', async () => {
    holdWindow()
    api.getTranscript.mockRejectedValue(Object.assign(new Error('forbidden'), { status: 403 }))
    const view = open()

    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).not.toContain('echo held-row'))
    expect(view.queryByRole('button', { name: '重试' })).toBeNull()
    expect(view.container.querySelector('.site-log')).toBeNull()
    expect(cachedWindow('room', null, 120)).toBeNull()
  })

  // 离线时留下来的那一窗，网回来后读到的是 403：权限在这期间被撤了，旧那几行同样要撤下。
  it('takes the held window down when a later refresh is refused', async () => {
    holdWindow()
    api.getTranscript.mockRejectedValue(new Error('offline'))
    const view = open()
    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).toContain('echo held-row'))

    api.getTranscript.mockReset()
    api.getTranscript.mockRejectedValue(Object.assign(new Error('forbidden'), { status: 403 }))
    tick.value++

    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).not.toContain('echo held-row'))
    expect(view.queryByRole('button', { name: '重试' })).toBeNull()
    expect(cachedWindow('room', null, 120)).toBeNull()
  })
})
