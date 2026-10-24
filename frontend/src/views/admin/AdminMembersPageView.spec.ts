/**
 * 后台「成员管理」那一屏拆出来的画法（`AdminMembersPageView.vue`）：props 进、事件出。
 *
 * 这一半只画：名单分两块、根那几行没有「移出」、状态列只在对不上时说话、读失败画在表
 * 格自己那一格而不是叠两条、还没回来画骨架、手上已有旧名单时只压暗不闪骨架、加人的框
 * 与移出确认框各自报「去做那件事」。取数与写在页面（`AdminMembersPage.vue`），这里一条
 * 请求都不打。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminMembersPageView from './AdminMembersPageView.vue'

import i18n, { setLocale } from '@/i18n'
import { MEMBERS_CANDIDATES, MEMBERS_ROSTER } from '@/views/demo/catalogScenesMisc2Fixtures'

const Blank = { template: '<div />' }
const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: Blank }],
  })

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。`base` 标成组件自己的 props 类型。 */
type AdminMembersPageViewProps = InstanceType<typeof AdminMembersPageView>['$props']
const base: AdminMembersPageViewProps = {
  loading: false,
  roster: MEMBERS_ROSTER,
  error: null,
  candidates: [],
  searching: false,
  adding: false,
  removing: null,
  dialogOpen: false,
  search: '',
  selected: [],
  confirmHandle: null,
  notice: null,
}

function mount(props: Partial<AdminMembersPageViewProps> = {}) {
  return render(AdminMembersPageView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n, router()] },
  })
}

beforeAll(() => {
  // happy-dom 里没有这几样，Vuetify 的浮层定位（两张对话框）会在挂载时读它们；缺了就抛。
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('成员管理', () => {
  it('还没回来时画骨架，不抢着画空态', () => {
    const view = mount({ ...base, roster: null, loading: true })
    expect(view.container.querySelector('.agrid__bone')).not.toBeNull()
    expect(view.queryByText(/还没有在页面上加过管理员/)).toBeNull()
  })

  it('读失败时那一条画在表格那一格、重试就在旁边；页头底下不再叠一条', async () => {
    const view = mount({ ...base, roster: null, error: '平台管理员名单接口超时' })
    expect(view.queryByText('成员加载失败')).not.toBeNull()
    expect(view.queryByText('平台管理员名单接口超时')).not.toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().load).toHaveLength(1)
  })

  it('手上已有名单时再取数只压暗（busy），不闪骨架', () => {
    const view = mount({ ...base, loading: true })
    expect(view.container.querySelector('.agrid--busy')).not.toBeNull()
    expect(view.container.querySelector('.agrid__bone')).toBeNull()
    expect(view.queryByText('林可')).not.toBeNull()
  })

  it('两组分开画，页头的计数是两块加起来', () => {
    const view = mount()
    expect(view.queryByText('部署配置里的根管理员')).not.toBeNull()
    expect(view.queryByText('页面上添加的')).not.toBeNull()
    expect(view.queryByText('林可')).not.toBeNull()
    expect(view.queryByText('爱丽丝')).not.toBeNull()
    // 2 根 + 1 页面上加。
    expect(view.queryByText('共 3 人')).not.toBeNull()
  })

  it('「移出」只出现在页面上加的那几行；根那几行写「不可移出」', () => {
    const view = mount()
    expect(view.getAllByRole('button', { name: '移出' })).toHaveLength(1)
    expect(view.getAllByText('不可移出').length).toBe(MEMBERS_ROSTER.root.length)
  })

  it('没账号的行把状态说出来，agent 那行挂徽章', () => {
    const view = mount()
    expect(view.queryByText('平台上没有这个账号')).not.toBeNull()
    expect(view.queryByText('AI 队友')).not.toBeNull()
  })

  it('按下「移出」报的是那一行的 handle 和触发它的事件', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '移出' }))
    const emitted = view.emitted().askRemove as unknown[][]
    expect(emitted[0][1]).toBe('alice')
  })

  it('「添加管理员」与「刷新」各自请页面去做那件事', async () => {
    const view = mount()
    await fireEvent.click(view.getByRole('button', { name: '添加管理员' }))
    expect(view.emitted().openDialog).toHaveLength(1)

    await fireEvent.click(view.getByRole('button', { name: '刷新' }))
    expect(view.emitted().load).toHaveLength(1)
  })

  it('加人框开着时画标题与那句后果说明；没有选中时主操作点不动', () => {
    const view = mount({ ...base, dialogOpen: true, candidates: MEMBERS_CANDIDATES, selected: [] })
    const dialog = view.getByRole('dialog')
    expect(within(dialog).getByText('添加管理员')).not.toBeNull()
    expect(within(dialog).getByText(/马上就能看到私密反馈和安全问题/)).not.toBeNull()

    const primary = within(dialog).getByRole('button', { name: '添加' }) as HTMLButtonElement
    expect(primary.disabled).toBe(true)
  })

  it('选好人之后「添加」报一声，不自己发请求', async () => {
    const view = mount({
      ...base,
      dialogOpen: true,
      candidates: MEMBERS_CANDIDATES,
      selected: [MEMBERS_CANDIDATES[0]!],
    })
    const dialog = view.getByRole('dialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: '添加' }))
    expect(view.emitted().addSelected).toHaveLength(1)
  })

  it('移出确认框：说清是谁、点确认报「去做移出」', async () => {
    const view = mount({ ...base, confirmHandle: 'alice' })
    const dialog = view.getByRole('dialog')
    expect(within(dialog).getByText('移出管理员')).not.toBeNull()
    expect(dialog.textContent).toContain('alice')

    await fireEvent.click(within(dialog).getByRole('button', { name: '移出' }))
    expect(view.emitted().remove).toHaveLength(1)
  })

  it('加删成功的提示画在名单上方', () => {
    const view = mount({ ...base, notice: '已添加：alice' })
    expect(view.queryByText('已添加：alice')).not.toBeNull()
  })
})
