/**
 * 管理台表格壳：管理端反馈队列和成员名单共用同一个。
 *
 * 抽这一层是因为有两件事**写错之后不会报错、只是看着不对**，所以得钉住：
 *
 * 1. **骨架是真的 `<tr>`**，和真行走同一份 `colgroup`。骨架换成一条 div 的话，
 *    数据到货那一刻整张表重排一次 —— 而骨架的全部意义就是不重排。这里断言的
 *    是「它在 `<tbody>` 里、格数等于列数」，不是「页面上有灰条」。
 * 2. **`empty` 与 `default` 槽互斥**：空态话术和真行同时画出来的话，一栏 0 条时
 *    下面会跟着上一页的行，而「暂无匹配的反馈」下面挂着三条反馈是最糟的那种。
 *
 * 有两件事 jsdom 量不到，用源码断言钉（照仓库老办法，见
 * `views/feedback/scroll.spec.ts`）：表头 `sticky`、滚动容器是这一层自己。
 * 卡片上**不能有** `overflow: hidden` —— 有的话 sticky 当场失效，圆角改由首末行
 * 自己画（下面也钉住，那是同一条约束的另一半）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AdminGrid from './AdminGrid.vue'

const here = dirname(fileURLToPath(import.meta.url))
const COLS = ['76px', null, '120px']

const mount = (props: Record<string, unknown> = {}, slots: Record<string, string> = {}) =>
  render(AdminGrid, {
    props: { cols: COLS, label: '测试列表', ...props },
    slots: {
      head: '<tr><th scope="col">ID</th><th scope="col">标题</th><th scope="col">状态</th></tr>',
      ...slots,
    },
  })

describe('管理台表格壳', () => {
  it('骨架是真行：每行格数等于列数，行数由 skeletonRows 定', () => {
    const { container } = mount({ loading: true, skeletonRows: 3 })
    const rows = Array.from(container.querySelectorAll('tbody .agrid__row'))
    expect(rows).toHaveLength(3)
    for (const row of rows) {
      expect(row.closest('tbody')).not.toBeNull()
      expect(row.querySelectorAll('td')).toHaveLength(COLS.length)
      expect(row.querySelectorAll('.agrid__bone')).toHaveLength(COLS.length)
    }
    // 加载中不画真行，也不画空态 —— 三个分支里只能亮一个。
    expect(container.querySelector('.agrid__none')).toBeNull()
  })

  it('骨架的骨头长度按 boneWidths 走，而不是那组兜底值', () => {
    const { container } = mount({
      loading: true,
      skeletonRows: 1,
      boneWidths: ['11%', '22%', '33%'],
    })
    const widths = Array.from(container.querySelectorAll('.agrid__bone')).map((el) => (el as HTMLElement).style.width)
    expect(widths).toEqual(['11%', '22%', '33%'])
  })

  it('空态是一行、跨满所有列、且不画 default 槽', () => {
    const { container, getByText } = mount(
      { empty: '暂无匹配的反馈' },
      { default: '<tr><td>这是真行，不该出现</td></tr>' }
    )
    const none = container.querySelector('.agrid__none')
    expect(none).not.toBeNull()
    expect(none!.getAttribute('colspan')).toBe(String(COLS.length))
    expect(getByText('暂无匹配的反馈')).toBeTruthy()
    expect(container.textContent).not.toContain('这是真行')
  })

  it('有内容时画 default 槽，不画空态', () => {
    const { container, getByText } = mount(
      { empty: null },
      { default: '<tr class="real"><td>真行</td><td>标题</td><td>已收录</td></tr>' }
    )
    expect(container.querySelector('.agrid__none')).toBeNull()
    expect(getByText('真行')).toBeTruthy()
  })

  it('busy 压暗已有内容，但既不换骨架也不清空', () => {
    const { container } = mount(
      { busy: true },
      { default: '<tr class="real"><td>真行</td><td>标题</td><td>已收录</td></tr>' }
    )
    expect(container.querySelector('.agrid')!.className).toContain('agrid--busy')
    expect(container.querySelectorAll('.agrid__bone')).toHaveLength(0)
    expect(container.querySelector('.real')).not.toBeNull()
    // 读屏也要知道它在忙，否则「点了没反应」。
    expect(container.querySelector('table')!.getAttribute('aria-busy')).toBe('true')
  })

  it('表格有一个可读的名字', () => {
    const { container } = mount()
    expect(container.querySelector('table')!.getAttribute('aria-label')).toBe('测试列表')
  })

  it('滚动容器是这一层自己，表头才 sticky 得住', () => {
    const src = readFileSync(join(here, 'AdminGrid.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    const rule = (selector: string) => {
      const at = src.indexOf(`${selector} {`)
      if (at < 0) throw new Error(`AdminGrid.vue 里找不到 ${selector}`)
      return src.slice(at, src.indexOf('}', at))
    }
    expect(rule('.agrid__scroll')).toContain('overflow: auto')
    expect(rule('.agrid__head :deep(th)')).toContain('position: sticky')
    expect(rule('.agrid__head :deep(th)')).toContain('background: var(--surface)')
    // 卡片自己**不能**有 overflow —— 那会给 sticky 造一个新的滚动上下文，
    // 表头就跟着卡片一起滚走了。
    expect(rule('.agrid')).not.toContain('overflow')
  })
})
