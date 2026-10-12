/**
 * 小队知识库那一屏拆出来的画法（`KnowledgeView.vue`）：props 进、事件出。
 *
 * 这一半只画：还在读的时候一个转圈、读失败整块换成失败的样子（不是一边说失败一边画
 * 「知识库暂无内容」）、空的两句话按「有没有筛选」分岔、网格与列表两种画法、工具栏三
 * 条筛选与上传把动作报上去。取数与写都在页面（`Knowledge.vue`）—— 所以这里一条请求都
 * 不打，测的是「谁报了什么」。
 *
 * 屏幕上的「筛选值」现在走四个具名 model 报给页面（原来是把 `filter.type` / `filter.tag`
 * 直接改在递上来的对象上）；测的是点一下报出的是哪一条、值是什么，不是它自己存没存。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import KnowledgeView from './KnowledgeView.vue'

import i18n, { setLocale } from '@/i18n'
import { KNOWLEDGE_OWNER_ID, KNOWLEDGE_ROWS, KNOWLEDGE_TAGS } from '@/views/demo/catalogKnowledgeFixtures'

const Blank = { template: '<div />' }
const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: Blank }],
  })

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。`base` 标成组件自己的 props 类型。 */
type KnowledgeViewProps = InstanceType<typeof KnowledgeView>['$props']
const base: KnowledgeViewProps = {
  loading: false,
  knowledges: KNOWLEDGE_ROWS,
  hasFilters: false,
  availableTags: KNOWLEDGE_TAGS,
  ownerId: KNOWLEDGE_OWNER_ID,
  failed: false,
  reason: null,
  forbidden: false,
  selectedResource: null,
  selectedResourceContent: {},
  uploading: false,
  searchQuery: '',
  viewMode: 'grid',
  typeFilter: null,
  tagFilter: null,
  resourceDetailDialog: false,
  uploadDialog: false,
}

function mount(props: Partial<KnowledgeViewProps> = {}) {
  return render(KnowledgeView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n, router()] },
  })
}

async function selectBox(view: ReturnType<typeof mount>, cls: string) {
  await waitFor(() => expect(view.container.querySelector(`${cls} input`)).toBeTruthy())
  return view.container.querySelector(`${cls} input`) as HTMLInputElement
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('知识库', () => {
  it('还在读的时候画转圈，不画「知识库暂无内容」', () => {
    const view = mount({ ...base, loading: true, knowledges: [] })
    expect(view.container.querySelector('.v-progress-circular')).not.toBeNull()
    expect(view.queryByText('知识库暂无内容')).toBeNull()
  })

  it('读失败整块换成失败的样子，点重试请页面重取', async () => {
    const view = mount({ ...base, failed: true, reason: '知识库接口超时', knowledges: [] })
    expect(view.queryByText('加载知识库失败')).not.toBeNull()
    expect(view.queryByText('知识库接口超时')).not.toBeNull()
    expect(view.queryByText('知识库暂无内容')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().change).toHaveLength(1)
  })

  it('没有权限时说的是没权限，也不给重试', () => {
    const view = mount({ ...base, failed: true, reason: null, forbidden: true, knowledges: [] })
    expect(view.queryByText('你没有权限查看')).not.toBeNull()
    expect(view.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('读回来了、一条都没有、也没筛过：说「暂无内容」并指出先去对话里放东西', () => {
    const view = mount({ ...base, knowledges: [], hasFilters: false })
    expect(view.queryByText('知识库暂无内容')).not.toBeNull()
    expect(view.container.textContent).toContain('在对话中把有价值的内容加入知识库')
  })

  it('读回来了、筛完没有：说的是「调整筛选条件」', () => {
    const view = mount({ ...base, knowledges: [], hasFilters: true })
    expect(view.container.textContent).toContain('请尝试调整筛选条件')
    expect(view.container.textContent).not.toContain('在对话中把有价值的内容加入知识库')
  })

  it('网格里一条一张卡', () => {
    const view = mount({ viewMode: 'grid' })
    expect(view.container.querySelectorAll('.resource-card')).toHaveLength(KNOWLEDGE_ROWS.length)
    expect(view.container.querySelectorAll('.resource-row')).toHaveLength(0)
  })

  it('列表视图画的是表格，每行都在', () => {
    const view = mount({ viewMode: 'table' })
    expect(view.container.querySelector('.resource-table')).not.toBeNull()
    expect(view.container.querySelectorAll('.resource-row')).toHaveLength(KNOWLEDGE_ROWS.length)
    expect(view.container.querySelectorAll('.resource-card')).toHaveLength(0)
  })

  it('资料类型那颗下拉：报出变了的那一条和值，并说一声「筛选变了」', async () => {
    const view = mount()
    await fireEvent.mouseDown(await selectBox(view, '.resource-type-filter'))
    await fireEvent.click(await view.findByRole('option', { name: '链接' }))

    const typed = view.emitted()['update:typeFilter'] as unknown[][]
    expect(typed[0][0]).toBe('LINK')
    expect(view.emitted().change).toHaveLength(1)
  })

  it('标签那颗下拉报的是那一条标签；搜索框报的是那一串字', async () => {
    const view = mount()
    await fireEvent.mouseDown(await selectBox(view, '.tag-filter'))
    await fireEvent.click(await view.findByRole('option', { name: '会议' }))
    const tagged = view.emitted()['update:tagFilter'] as unknown[][]
    expect(tagged[0][0]).toBe('会议')

    const search = view.container.querySelector('.knowledge-search input') as HTMLInputElement
    await fireEvent.update(search, '设计')
    const queried = view.emitted()['update:searchQuery'] as unknown[][]
    expect(queried[queried.length - 1]![0]).toBe('设计')
  })

  it('点卡片报出「打开了哪一条」', async () => {
    const view = mount()
    await fireEvent.click(view.container.querySelector('.resource-card') as HTMLElement)
    const opened = view.emitted().open as unknown[][]
    expect(opened[0][0]).toMatchObject({ id: KNOWLEDGE_ROWS[0]!.id })
  })

  it('切视图报出「换成哪一种」', async () => {
    const view = mount()
    await waitFor(() => expect(view.container.querySelectorAll('.v-btn-toggle button').length).toBe(2))
    await fireEvent.click(view.container.querySelectorAll('.v-btn-toggle button')[1]!)
    const mode = view.emitted()['update:viewMode'] as unknown[][]
    expect(mode[0][0]).toBe('list')
    // 具名 model 没有父级接线时本地也换：网格让位给表格。
    expect(view.container.querySelector('.resource-table')).not.toBeNull()
  })

  it('「上传资料」只请页面开框', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '上传资料' }))
    expect(view.emitted().upload).toHaveLength(1)
  })
})
