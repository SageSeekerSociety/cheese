// 逐文件 diff 那一列：行数过了门槛就交给 VirtualList（虚拟化），不到门槛照旧整列画
// ——整列都在 DOM 里时浏览器 Ctrl+F 和读屏都摸得到每一行。
//
// happy-dom 不排版，量不出「屏幕上挂着几行」。这里问的是**代码走到了哪条路**：行数
// 过没过门槛、递给 virtua 的数据对不对。`virtua/vue` 在这里换成记账替身（同
// common/VirtualList.spec.ts），收到的数据记下来，槽里的行原样画出来。
import type { Component } from 'vue'
import type { DiffLine } from '../../lib/diff'

import { nextTick } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DIFF_WINDOW } from '../../lib/diff'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '../../lib/virtualList'

import { setLocale } from '@/i18n'

const virtua = vi.hoisted(() => ({
  /** 每次交给 virtua 的那一份数据，按顺序记下来。 */
  seen: [] as Record<string, unknown>[],
}))

vi.mock('virtua/vue', async () => {
  const { defineComponent: define, h: hyperscript } = await import('vue')
  return {
    Virtualizer: define({
      name: 'Virtualizer',
      props: ['data', 'itemSize', 'bufferSize', 'keepMounted', 'itemProps', 'scrollRef', 'item', 'as', 'role'],
      setup(props, { attrs, slots }) {
        // 记的是**那份 props 对象**，不是当时那一份数据：Virtualizer 的实例在整份
        // diff 铺开时不会重建，`data` 是原地换的（同 common/VirtualList.spec.ts）。
        virtua.seen.push(props as unknown as Record<string, unknown>)
        return () =>
          hyperscript(
            'div',
            { 'data-virtua': 'Virtualizer', ...attrs },
            ((props.data as unknown[]) ?? []).map((item, index) => hyperscript('div', slots.default?.({ item, index })))
          )
      },
    }),
  }
})

const dataOf = (at: number) => virtua.seen.at(at)?.data as unknown[] | undefined

import ChangesDiff from './ChangesDiff.vue'

const Diff = ChangesDiff as unknown as Component

/** 一段 hunk：一个组头 + n 行正文。渲染出来是 n + 1 个 `.diff-line`。 */
function diffLines(n: number): DiffLine[] {
  const rows: DiffLine[] = [{ kind: 'hunk', text: `@@ -1,${Math.max(n, 1)} +1,${Math.max(n, 1)} @@` }]
  for (let i = 0; i < n; i += 1) rows.push({ kind: 'context', text: `line ${i}` })
  return rows
}

const rowsIn = (container: Element) => container.querySelectorAll('.diff-line').length

beforeEach(() => {
  setLocale('zh-CN')
  virtua.seen.length = 0
})

describe('行数不到门槛', () => {
  it('整列画出来，一行不少，也不碰 virtua', async () => {
    // 门槛是「超过」不是「达到」：阈值以内整列都在 DOM 里，Ctrl+F 和读屏摸得到每一行
    // ——这正是门槛存在的理由。
    const { container } = render(Diff, { props: { lines: diffLines(VIRTUAL_LIST_CONTENT_THRESHOLD - 1) } })
    await nextTick()
    expect(rowsIn(container)).toBe(VIRTUAL_LIST_CONTENT_THRESHOLD)
    expect(virtua.seen).toHaveLength(0)
  })
})

describe('行数过了门槛', () => {
  it('整列交给 virtua，数据原样递过去', async () => {
    // 滚动容器是一个模板 ref：挂完那一帧才落地，VirtualList 因此会先整列画一遍、下一
    // 帧再交给 virtua（见它的文件头）。所以这里要等那一帧。
    const { container } = render(Diff, { props: { lines: diffLines(VIRTUAL_LIST_CONTENT_THRESHOLD + 51) } })
    await nextTick()
    expect(virtua.seen).toHaveLength(1)
    expect(dataOf(0)).toHaveLength(VIRTUAL_LIST_CONTENT_THRESHOLD + 52)
    expect(container.querySelector('[data-virtua]')).not.toBeNull()
  })
})

describe('长文件的窗口', () => {
  it('没点开前只铺 DIFF_WINDOW 行，收着的那一截不画', async () => {
    render(Diff, { props: { lines: diffLines(DIFF_WINDOW + 300) } })
    await nextTick()
    expect(dataOf(0)).toHaveLength(DIFF_WINDOW)
  })

  it('点开「显示剩余」：整份交给 virtua，还是那一份数据，只是长了', async () => {
    const { container, getByRole } = render(Diff, { props: { lines: diffLines(DIFF_WINDOW + 300) } })
    await nextTick()
    expect(dataOf(0)).toHaveLength(DIFF_WINDOW)

    await fireEvent.click(getByRole('button'))
    expect(dataOf(-1)).toHaveLength(DIFF_WINDOW + 301)
    // 长文件整份铺开也是虚拟化的：屏幕外的行不挂在 DOM 上。
    expect(container.querySelector('[data-virtua]')).not.toBeNull()
  })
})
