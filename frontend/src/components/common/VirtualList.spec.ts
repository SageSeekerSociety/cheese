// 通用虚拟列表：什么时候整列画、什么时候交给 virtua、行怎么认、键盘走到哪。
//
// happy-dom 不排版，所以这里**不量**「屏幕上到底挂着几行」——那是 virtua 自己按滚动
// 位置算出来的，量出来的只会是我们喂给它的假高度。这一份问的是**代码走到了哪条路**：
// 行数过没过门槛、有没有滚动容器、递过去的几个 props 对不对、按序号滚动有没有真的落
// 到 virtua 手上。为了看得见这些，`virtua/vue` 在这里被换成一个记账的替身：收到的
// props 记下来，槽里的行原样画出来，别的什么都不做。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import VirtualList, { type VirtualListHandle } from './VirtualList.vue'

const virtua = vi.hoisted(() => ({
  /** 每一次交给 virtua 的记录：哪个组件、收到什么 props。 */
  seen: [] as { which: string; props: Record<string, unknown> }[],
  /** 替身那只手，用来断言按序号滚动有没有递下去。 */
  scrollToIndex: vi.fn(),
}))

vi.mock('virtua/vue', async () => {
  const { defineComponent: define, h: hyperscript } = await import('vue')
  const make = (which: 'VList' | 'Virtualizer') =>
    define({
      name: which,
      props: ['data', 'itemSize', 'bufferSize', 'shift', 'keepMounted', 'itemProps', 'scrollRef'],
      setup(props, { attrs, slots, expose }) {
        virtua.seen.push({ which, props: props as unknown as Record<string, unknown> })
        expose({ scrollToIndex: virtua.scrollToIndex })
        // 替身把每一行的盒子画出来，并把 `itemProps`（记着序号的那个属性）挂上去——
        // 真实 virtua 也这么干，所以「焦点回到窗口外那一行」那条路在这里走得通。
        const itemProps = props.itemProps as ((item: string, index: number) => Record<string, unknown>) | undefined
        return () =>
          hyperscript(
            'div',
            { 'data-virtua': which, ...attrs },
            ((props.data as string[]) ?? []).map((item, index) =>
              hyperscript('div', { ...(itemProps?.(item, index) ?? {}) }, slots.default?.({ item, index }))
            )
          )
      },
    })
  return { VList: make('VList'), Virtualizer: make('Virtualizer') }
})

const SCROLL_PARENT = document.createElement('div')

function itemsOf(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `t${i}`)
}

const itemKey = (item: string): string => item

/**
 * 挂一份 VirtualList，把外面那只手要回来（露出方法的那一端在模板 ref 上，`render()`
 * 拿不到组件实例），并把要画的那一列做成 Host 的 props，好让测试换一份数据重画。
 */
function draw(props: Record<string, unknown>, items: string[] = itemsOf(100)) {
  const handle: { current: VirtualListHandle | null } = { current: null }
  const Host = defineComponent({
    name: 'VirtualListHost',
    props: { items: { type: Array, required: true } },
    setup(hostProps) {
      return () =>
        h(
          // 按 Vue 的 `Component` 收进来，好让 props 走 `RawProps` 那一支：这里本来就是
          // 拿一份宽松的对象去喂各种组合，不需要 `DefineComponent` 按 props 逐项校验。
          VirtualList as Component,
          {
            ...props,
            items: hostProps.items,
            ref: (el: unknown) => (handle.current = el as VirtualListHandle | null),
          },
          { item: ({ item }: { item: string }) => h('div', { class: 'row' }, item) }
        )
    },
  })
  return { ...render(Host, { props: { items } }), handle }
}

function rowTexts(container: Element): (string | null)[] {
  return Array.from(container.querySelectorAll('.row'), (r) => r.textContent)
}

beforeEach(() => {
  virtua.seen.length = 0
  virtua.scrollToIndex.mockClear()
})

describe('门槛', () => {
  it('行数不到门槛：整列画出来，一行不少，也不碰 virtua', () => {
    // 门槛是「超过」不是「达到」：100 行还整列画。这一条钉住的是另一句话——阈值以内
    // 的 DOM 和没接虚拟列表时一模一样（FLIP 换位、Tab 走位都还在）。
    const { container } = draw({ itemKey }, itemsOf(100))
    expect(container.querySelectorAll('.row')).toHaveLength(100)
    expect(virtua.seen).toHaveLength(0)
  })

  it('超过门槛、也知道谁在滚：整列交给 virtua，数据原样递过去', () => {
    draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    expect(virtua.seen.map((s) => s.which)).toEqual(['Virtualizer'])
    expect(virtua.seen[0].props.data).toHaveLength(150)
  })

  it('超过门槛但没给滚动容器：照旧整列画 —— 不知道按谁的窗口算就不猜', () => {
    // 猜一个（自己起个能滚的盒子）会把外面那层滚的东西挪走：话题列表的置顶行和组头
    // 就会钉在原地不动。宁可不虚拟化。
    const { container } = draw({ itemKey }, itemsOf(150))
    expect(virtua.seen).toHaveLength(0)
    expect(container.querySelectorAll('.row')).toHaveLength(150)
  })
})

describe('递给 virtua 的那几个数', () => {
  it('估计行高、缓冲、换位、常驻行和滚动容器都照传', () => {
    draw(
      {
        itemKey,
        scrollParent: SCROLL_PARENT,
        estimatedSize: 44,
        bufferSize: 320,
        shift: true,
        keepMounted: [7],
      },
      itemsOf(150)
    )
    const p = virtua.seen[0].props
    expect(p.itemSize).toBe(44)
    expect(p.bufferSize).toBe(320)
    expect(p.shift).toBe(true)
    expect(p.keepMounted).toEqual([7])
    expect(p.scrollRef).toBe(SCROLL_PARENT)
  })

  it('每一行的盒子上记着它排第几', () => {
    const { container } = draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    expect(container.querySelectorAll('[data-vlist-index]')).toHaveLength(150)
  })
})

describe('键盘走到窗口外的行上', () => {
  it('按序号把那一行带回来', () => {
    // Tab 过去、或者长按菜单把焦点还回某一行的输入框：那一行不在窗口里就没有 DOM
    // 可 focus，virtua 得先把它挂出来。这里断言的就是那一下按序号滚动。
    const { container } = draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    const offscreen = container.querySelector('[data-vlist-index="149"]')
    expect(offscreen).not.toBeNull()
    fireEvent.focusIn(offscreen!)
    expect(virtua.scrollToIndex).toHaveBeenCalledWith(149, { align: 'nearest' })
  })

  it('焦点落在行外面（比如列表自己）时什么都不做', () => {
    const { container } = draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    fireEvent.focusIn(container.querySelector('[data-virtua]')!)
    expect(virtua.scrollToIndex).not.toHaveBeenCalled()
  })
})

describe('按序号滚动', () => {
  it('默认滚到最近 —— 和 scrollIntoView({ block: "nearest" }) 一个意思', () => {
    const { handle } = draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    handle.current!.scrollToIndex(12)
    expect(virtua.scrollToIndex).toHaveBeenCalledWith(12, { align: 'nearest' })
  })

  it('要滚到哪一头由调用方说了算', () => {
    const { handle } = draw({ itemKey, scrollParent: SCROLL_PARENT }, itemsOf(150))
    handle.current!.scrollToIndex(3, { align: 'center' })
    expect(virtua.scrollToIndex).toHaveBeenLastCalledWith(3, { align: 'center' })
  })
})

describe('整列渲染那一层', () => {
  it('行按 itemKey 认：换一份数据只有顺序变了，DOM 跟着换位而不是重画', async () => {
    // FLIP 动画要看得见，前提是「同一行换了个位置」这件事 Vue 认得出——靠的就是这个
    // key。这里验的是 key 真的按 itemKey 发的：把顺序倒过来，DOM 里的顺序跟着倒。
    const { container, rerender } = draw({ itemKey }, ['a', 'b', 'c'])
    expect(rowTexts(container)).toEqual(['a', 'b', 'c'])
    await rerender({ items: ['c', 'b', 'a'] })
    expect(rowTexts(container)).toEqual(['c', 'b', 'a'])
  })

  it('带过渡时也照画不误（阈值以内留给 TransitionGroup 演换位）', () => {
    const { container } = draw({ itemKey, transition: 'rail-row', transitionKey: 'p1' }, ['a', 'b', 'c'])
    expect(rowTexts(container)).toEqual(['a', 'b', 'c'])
    expect(virtua.seen).toHaveLength(0)
  })
})
