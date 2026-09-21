// 知识库是团队详情里最大的一页：网格/列表两种画法、一个详情对话框、一个上传对话框。
// 顺带守住一处行为改动：筛选框以前拿中文标签当值，现在值就是 KnowledgeType 代码，
// 所以「选 Link 就要发出 type=LINK」必须真的发出去，否则换英文之后筛选会静默失效。
import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  upload: vi.fn(),
  confirm: vi.fn(),
  alert: vi.fn(),
}))
// 不能整块替掉 vue-router：间接引到的 @/router 还要 createRouter。
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ params: { teamId: '1' } }),
}))
vi.mock('@/network/api/knowledges', () => ({
  KnowledgesApi: { list: mocks.list, create: vi.fn(), deleteKnowledge: vi.fn() },
}))
vi.mock('@/network/api/materials', () => ({ MaterialsApi: { upload: mocks.upload } }))
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: mocks.confirm, alert: mocks.alert }),
}))
vi.mock('@/services/account', () => ({ currentUserId: ref(9) }))

import Knowledge from './Knowledge.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

/** 昨天同一时刻再早一分钟，免得刚好卡在日界上被算成「今天」。 */
const yesterdayTime = () => dayjs().subtract(1, 'day').subtract(1, 'minute').valueOf()

/** 一条链接资料，挂着原始讨论，正好把详情对话框里最长的几段都点亮。 */
function linkKnowledge(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'Launch checklist',
    description: 'Steps before we ship.',
    type: 'LINK',
    content: JSON.stringify({
      url: 'https://example.com/checklist',
      title: 'Launch checklist',
      description: 'Steps before we ship.',
    }),
    labels: ['process', 'release'],
    createdAt: Date.UTC(2023, 11, 1, 6, 30),
    creator: { id: 9, nickname: 'Nina', avatarId: null },
    sourceChannel: null,
    originalMessage: {
      content: 'Here is the checklist.',
      createdAt: yesterdayTime(),
      sender: { nickname: 'Nina', avatarId: null },
    },
    ...over,
  }
}

function mountPage(knowledges: unknown[] = [linkKnowledge()]) {
  mocks.list.mockResolvedValue({
    data: {
      knowledges,
      page: { pageStart: 0, pageSize: 20, hasMore: false, nextStart: null, total: knowledges.length },
    },
  })
  return render(Knowledge, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { TipTapEditor: { template: '<div />' } },
    },
  })
}

/** 卡片整块都能点开详情，点标题最省事。 */
async function openDetail(view: ReturnType<typeof mountPage>, title: string) {
  const card = (await view.findByText(title)).closest('.resource-card')
  if (!card) throw new Error(`no resource card for ${title}`)
  await fireEvent.click(card)
}

/**
 * 网格/列表切换是两个只有图标的按钮，按位置取第二个（列表）。
 * 工具栏要等 loading 落下来才渲染，所以先等一个必然出现的东西。
 */
async function switchToList(view: ReturnType<typeof mountPage>) {
  await view.findByText('Launch checklist')
  const buttons = view.container.querySelectorAll('.v-btn-toggle .v-btn')
  if (buttons.length !== 2) throw new Error(`expected 2 view toggles, found ${buttons.length}`)
  await fireEvent.click(buttons[1])
}

/** Vuetify 的字段标签会渲染两遍（一个 floating 副本标了 aria-hidden），按文本查必中两个。 */
function expectText(root: ReturnType<typeof within>, text: string) {
  expect(root.getAllByText(text).length).toBeGreaterThan(0)
}

beforeEach(() => {
  // happy-dom 两个都不给，而 Vuetify 的下拉/对话框定位会真的去读它们。
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('teams/detail/Knowledge', () => {
  it('中文下空知识库给的是中文的引导文案', async () => {
    setLocale('zh-CN')
    const view = mountPage([])

    await view.findByText('知识库暂无内容')
    await view.findByText('在频道聊天中添加有价值的内容到知识库，方便团队随时查阅')
    expect(view.getByText('上传资料')).toBeTruthy()
  })

  it('中文下详情对话框里类型、来源和原始讨论都在', async () => {
    setLocale('zh-CN')
    const view = mountPage()

    await view.findByText('Launch checklist')
    await openDetail(view, 'Launch checklist')

    await view.findByText('资料信息')
    await view.findByText('未知频道')
    await view.findByText(/^昨天 \d{2}:\d{2}$/)
    expect(view.getAllByText('链接').length).toBeGreaterThan(0)
    expect(view.getByText('来源频道')).toBeTruthy()
    expect(view.getByText('删除资料')).toBeTruthy()
    expect(view.getByText('打开资料')).toBeTruthy()
  })

  it('英文下网格视图和详情对话框都没有汉字', async () => {
    setLocale('en')
    const view = mountPage()

    await view.findByText('Launch checklist')
    await openDetail(view, 'Launch checklist')

    await view.findByText('Resource info')
    await view.findByText('Unknown channel')
    await view.findByText('Original discussion')
    await view.findByText(/^Yesterday \d{2}:\d{2}$/)
    expect(view.getByText('Source channel')).toBeTruthy()
    expect(view.getByText('Visit link')).toBeTruthy()
    expect(view.getByText('Delete resource')).toBeTruthy()
    expect(view.getByText('Open resource')).toBeTruthy()
    // 「12/01/2023, 02:30 PM」这类本地化格式里不该再冒出「年」「月」「日」。
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下列表视图的表头和类型名都翻到了', async () => {
    setLocale('en')
    const view = mountPage()

    await switchToList(view)

    await view.findByText('Actions')
    for (const header of ['Resource name', 'Type', 'Added by', 'Added at', 'Tags']) {
      expect(view.getByText(header)).toBeTruthy()
    }
    expect(view.getByText('Nina')).toBeTruthy()
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下上传对话框没有汉字', async () => {
    setLocale('en')
    const view = mountPage()

    await view.findByText('Add a resource')
    await fireEvent.click(view.getAllByText('Add a resource')[0])

    // 工具栏上的筛选框也叫「Resource type」，所以按对话框自己的壳来查。
    const dialog = await waitFor(() => {
      const el = document.body.querySelector('.upload-dialog')
      if (!el) throw new Error('upload dialog not rendered')
      return el
    })
    const inDialog = within(dialog as HTMLElement)

    expectText(inDialog, 'Resource type')
    for (const label of ['Resource name', 'Choose a file', 'More details', 'Cancel', 'Upload']) {
      expectText(inDialog, label)
    }
    for (const type of ['File', 'Text', 'Link', 'Code snippet']) {
      expectText(inDialog, type)
    }
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下按类型筛选发出的是类型代码而不是标签', async () => {
    setLocale('en')
    const view = mountPage()

    const filters = await view.findAllByRole('combobox')
    await fireEvent.mouseDown(filters[0])
    await fireEvent.click(await view.findByRole('option', { name: 'Link' }))

    await waitFor(() => expect(mocks.list.mock.calls.at(-1)?.[0]?.type).toBe('LINK'))
  })
})
