/** 工作面板那条 tab 栏整条跟着语言走。
 *
 * 这一份真的把这格挂起来渲染一遍，扫的是**整条 tab 栏**：五个格子的名字、格子上
 * 的计数、以及 hover / 读屏才听得见的整句 `title`。tab 栏上再没有别的东西，所以
 * 这里可以整块扫「一个汉字都不剩」——不像整页，页面上还有别的切片欠着的字。
 *
 * 带计数的几句英文写的是「零 / 一 / 多」三截（总览、改动），所以按件数各来一遍。
 * 它们唯一的出口是 `title`，别处看不到，只能从这里过。
 *
 * 还有一条边界这里守得住：**格子的名字和整句 title 里那个名字是同一个词的两次
 * 出现**。名字走 `workspace.tabs.*`，整句走 `workspace.tabTitle.*`（句子里把名字
 * 写死了，不是 `{tab}` 占位符——占了位就写不了多形态，见
 * docs/i18n-glossary.md §3），所以两处一起断言：改了一处忘另一处，至少红一条。
 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  getPreview: vi.fn(),
  getTopicWorkSummary: vi.fn(),
  listRoomTasks: vi.fn(),
}))

import { getPreview, getTopicWorkSummary, listRoomTasks } from '../api'

import WorkPanel from './WorkPanel.vue'

import { setLocale } from '@/i18n'

const Panel = WorkPanel as unknown as Component

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

/** `listRoomTasks` 回的是 `RoomTask & { blocks: Block[] }`（api.ts）——每条支线带着
 *  自己那几块对话。面板只数件数、不看块，所以给个空的，形状对上就行。 */
function roomTask(status: string) {
  return {
    id: `task-${status}`,
    project_id: 'p1',
    room_id: 't1',
    title: 't',
    status,
    owner_handle: 'u',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    blocks: [],
  } as RoomTask & { blocks: never[] }
}

const preview = (artifact_id: string) => ({ path: 'report.html', mime: 'text/html', artifact_id })

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  vi.mocked(getPreview).mockReset()
  vi.mocked(getTopicWorkSummary).mockReset()
  vi.mocked(listRoomTasks).mockReset()
  // 五格齐全的一格：对话（withChat）在、芝士在跑（现场）、有改动、有预览。
  vi.mocked(getPreview).mockResolvedValue(preview('a1'))
  vi.mocked(getTopicWorkSummary).mockResolvedValue({ changed_files: ['src/a.ts'], has_run: true })
  vi.mocked(listRoomTasks).mockResolvedValue({ data: [roomTask('closed')], total: 1 })
})

function mount() {
  return render(Panel, {
    props: { topic, activityTick: 0, withChat: true, working: true },
    slots: { chat: '<div data-testid="chat-pane">pane</div>' },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

const bar = (container: Element) => container.querySelector('.tabbar') as HTMLElement
/** 这一栏整条的字。空白全折掉：格子名和它后面那个计数之间有模板给的换行，而那份
 *  空白不是这条例用要钉的东西。 */
const barText = (container: Element) => (bar(container).textContent ?? '').replace(/\s+/g, '')
const tabButtons = (container: Element) => Array.from(bar(container).querySelectorAll('.tabbar__tab'))
/** 每一格的名字。计数那个 span 紧跟在名字后面，所以名字 = 整格的字减掉计数。 */
function labels(container: Element): string[] {
  return tabButtons(container).map((b) => {
    const full = (b.textContent ?? '').trim()
    const count = b.querySelector('.tabbar__count')?.textContent?.trim() ?? ''
    return count ? full.replace(count, '').trim() : full
  })
}
/** hover / 读屏听到的那句整话，和格子上的名字是两个来源。 */
function titles(container: Element): string[] {
  return tabButtons(container).map((b) => b.getAttribute('title') ?? '')
}

/** 一轮跑完（working 由真转假）之后面板会重新拉一遍预览和改动——借这一下拿到
 *  「有新内容」「有新改动」那两张脸。 */
async function turnEnds(view: ReturnType<typeof mount>) {
  vi.mocked(getPreview).mockResolvedValue(preview('a2'))
  vi.mocked(getTopicWorkSummary).mockResolvedValue({ changed_files: ['src/a.ts', 'src/b.ts'], has_run: true })
  await view.rerender({ topic, activityTick: 0, withChat: true, working: false })
  await flush()
}

describe('讲中文', () => {
  it('五格的名字、计数和整句 title 都是中文', async () => {
    setLocale('zh-CN')
    const view = mount()
    await flush()

    expect(labels(view.container)).toEqual(['对话', '总览', '现场', '改动', '预览'])
    expect(barText(view.container)).toContain('总览1')
    expect(titles(view.container)).toEqual([
      '对话',
      '总览（1 件任务）',
      '现场（芝士正在工作）',
      '改动（1 个文件）',
      '预览',
    ])

    await turnEnds(view)
    expect(titles(view.container)).toEqual([
      '对话',
      '总览（1 件任务）',
      '现场',
      '改动（2 个文件，有新改动）',
      '预览（有新内容）',
    ])
  })
})

describe('讲英文', () => {
  it('整条 tab 栏一个汉字都不剩', async () => {
    setLocale('en')
    const view = mount()
    await flush()

    expect(labels(view.container)).toEqual(['Chat', 'Overview', 'Activity', 'Changes', 'Preview'])
    expect(titles(view.container)).toEqual([
      'Chat',
      'Overview (1 task)',
      'Activity (Cheese is working)',
      'Changes (1 file)',
      'Preview',
    ])
    expect(CJK.test(barText(view.container)), barText(view.container)).toBe(false)
    // title 不在 textContent 里，单独扫一遍：这一栏的字有一半在属性上。
    const allTitles = titles(view.container).join(' ')
    expect(CJK.test(allTitles), allTitles).toBe(false)
  })

  it('一轮跑完：多件形态和「有新内容」「有新改动」也都是英文', async () => {
    setLocale('en')
    const view = mount()
    await flush()

    await turnEnds(view)

    expect(titles(view.container)).toEqual([
      'Chat',
      'Overview (1 task)',
      'Activity',
      'Changes (2 files, new changes)',
      'Preview (new content)',
    ])
    expect(CJK.test(barText(view.container)), barText(view.container)).toBe(false)
    const allTitles = titles(view.container).join(' ')
    expect(CJK.test(allTitles), allTitles).toBe(false)
  })

  it('两件活、一件在跑：两个数各自对上', async () => {
    setLocale('en')
    vi.mocked(listRoomTasks).mockResolvedValue({ data: [roomTask('open'), roomTask('closed')], total: 2 })
    const view = mount()
    await flush()

    // 这句有两个占位符，写不了多形态，走的是词表里记着的 `(s)` 写法。
    expect(titles(view.container)[1]).toBe('Overview (2 task(s), 1 in progress)')
    // 计数写在格子上：名字后面跟的就是件数。
    expect(barText(view.container)).toContain('Overview2')
  })
})

describe('切一次语言', () => {
  it('已经画出来的名字和 title 当场跟着换', async () => {
    setLocale('zh-CN')
    const view = mount()
    await flush()
    expect(labels(view.container)[1]).toBe('总览')

    setLocale('en')
    await vi.waitFor(() => expect(labels(view.container)[1]).toBe('Overview'))
    expect(labels(view.container)).toEqual(['Chat', 'Overview', 'Activity', 'Changes', 'Preview'])
    expect(titles(view.container)[3]).toBe('Changes (1 file)')
    expect(CJK.test(barText(view.container)), barText(view.container)).toBe(false)
  })
})
