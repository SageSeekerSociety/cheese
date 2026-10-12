/**
 * 后台「空间申请」那一屏拆出来的画法（`AdminSpacesPageView.vue`）：props 进、事件出。
 *
 * 这一屏只有一件事：一条申请一眼看完，然后按通过或驳回。所以测的是：待审核的行才给
 * 动作、动作報的是**那一条**、没读到就不画成「暂无申请」；驳回框要一句理由，理由空着
 * 时那颗主操作点不动；只有一页时不留一排灰按钮。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminSpacesPageView from './AdminSpacesPageView.vue'

import i18n, { setLocale } from '@/i18n'
import { APPLICATIONS } from '@/views/demo/catalogScenesMiscFixtures'

/** 每一条 props 都给：`render` 的 `props` 是必填的组件 props，测试只想改一两样时
 *  从这一份上盖。`selected` 标成组件自己的类型。 */
type AdminSpacesPageViewProps = InstanceType<typeof AdminSpacesPageView>['$props']
const base: AdminSpacesPageViewProps = {
  status: 'PENDING',
  items: [],
  loading: false,
  loadError: null,
  offset: 0,
  hasMore: false,
  saving: false,
  pageSize: 50,
  writeError: '',
  selected: null,
  reason: '',
}

function mount(props: Partial<AdminSpacesPageViewProps> = {}) {
  return render(AdminSpacesPageView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeAll(() => {
  // happy-dom 里没有这几样，Vuetify 的遮罩定位（驳回框）会在挂载时读它们；缺了就抛。
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

describe('空间申请', () => {
  it('首屏（一条都没有、正在读）画骨架，不说「暂无申请」', () => {
    const view = mount({ ...base, loading: true })
    expect(view.container.querySelector('.asp__bone')).not.toBeNull()
    expect(view.queryByText('暂无申请。')).toBeNull()
  })

  it('读失败只说读不到，不画成「暂无申请」', async () => {
    const view = mount({ ...base, loadError: '连接被拒绝' })
    expect(view.queryByText('空间申请加载失败')).not.toBeNull()
    expect(view.queryByText('连接被拒绝')).not.toBeNull()
    expect(view.queryByText('暂无申请。')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(view.emitted().retry).toHaveLength(1)
  })

  it('读回来了、一条都没有，才说「暂无申请。」', () => {
    const view = mount({ ...base, items: [] })
    expect(view.queryByText('暂无申请。')).not.toBeNull()
  })

  it('待审核的那条才给动作，「通过」报的是那一条', async () => {
    const view = mount({ ...base, items: APPLICATIONS })
    // 三条里只有 PENDING 那一条有「通过 / 驳回」。
    const approves = view.getAllByRole('button', { name: '通过' })
    expect(approves).toHaveLength(1)

    await fireEvent.click(approves[0])
    const emitted = view.emitted().approve as unknown[][]
    expect(emitted[0][0]).toMatchObject({ id: 1, name: '计算机视觉兴趣组' })
  })

  it('「驳回」只是请页面把框打开，报的也是那一条', async () => {
    const view = mount({ ...base, items: APPLICATIONS })
    await fireEvent.click(view.getByRole('button', { name: '驳回' }))
    const emitted = view.emitted().reject as unknown[][]
    expect(emitted[0][0]).toMatchObject({ id: 1 })
  })

  it('驳回框：理由空着时主操作点不动，写上了才报「提交驳回」', async () => {
    const empty = mount({ ...base, items: APPLICATIONS, selected: APPLICATIONS[0], reason: '' })
    const dialog = empty.getByRole('dialog')
    const primary = within(dialog).getByRole('button', { name: '驳回' }) as HTMLButtonElement
    expect(primary.disabled).toBe(true)

    empty.unmount()
    const filled = mount({ ...base, items: APPLICATIONS, selected: APPLICATIONS[0], reason: '请补充计划' })
    const filledDialog = filled.getByRole('dialog')
    const go = within(filledDialog).getByRole('button', { name: '驳回' })
    await fireEvent.click(go)
    expect(filled.emitted()['submit-reject']).toHaveLength(1)
  })

  it('切页签请页面换一堆', async () => {
    const view = mount({ ...base, items: [] })
    await fireEvent.click(view.getByRole('tab', { name: '已通过' }))
    const emitted = view.emitted()['change-status'] as unknown[][]
    expect(emitted[0][0]).toBe('APPROVED')
  })

  it('分页：一页装得下（没有下一页）时不留那一排灰按钮', () => {
    const view = mount({ ...base, offset: 0, hasMore: false })
    expect(view.queryByRole('button', { name: '下一页' })).toBeNull()
    expect(view.queryByRole('button', { name: '上一页' })).toBeNull()
  })

  it('分页：后面还有时，报的步长是这一页的大小', async () => {
    const view = mount({ ...base, offset: 50, hasMore: true, pageSize: 50 })
    await fireEvent.click(view.getByRole('button', { name: '上一页' }))
    await fireEvent.click(view.getByRole('button', { name: '下一页' }))
    const emitted = view.emitted().page as unknown[][]
    expect(emitted[0][0]).toBe(-50)
    expect(emitted[1][0]).toBe(50)
  })
})
