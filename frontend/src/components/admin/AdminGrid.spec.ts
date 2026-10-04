/// <reference types="node" />
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
    const src = readFileSync(join(here, '../base/BaseTable.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    const rule = (selector: string) => {
      const at = src.indexOf(`${selector} {`)
      if (at < 0) throw new Error(`BaseTable.vue 里找不到 ${selector}`)
      return src.slice(at, src.indexOf('}', at))
    }
    expect(rule('.agrid__scroll')).toContain('overflow: auto')
    expect(rule('.agrid__head :deep(th)')).toContain('position: sticky')
    expect(rule('.agrid__head :deep(th)')).toContain('background: var(--surface)')
    // 卡片自己**不能**有 overflow —— 那会给 sticky 造一个新的滚动上下文，
    // 表头就跟着卡片一起滚走了。
    expect(rule('.agrid')).not.toContain('overflow')
  })

  it('state="error" 画 #error 槽、不画真行也不画空态', () => {
    const { container, getByText, queryByText } = mount(
      { state: 'error' },
      {
        default: '<tr class="real"><td>真行</td><td>标题</td><td>已收录</td></tr>',
        error: '<div class="err">网关不可达</div>',
        empty: '<div class="emp">暂无匹配的反馈</div>',
      }
    )
    expect(getByText('网关不可达')).toBeTruthy()
    expect(container.querySelector('.agrid__state')).not.toBeNull()
    expect(container.querySelector('.real')).toBeNull()
    // 读失败和「一条都没有」是两句话，同时画出来是最说不清的那种。
    expect(queryByText('暂无匹配的反馈')).toBeNull()
    expect(container.querySelector('.agrid__none')).toBeNull()
  })

  it('state="empty" 画 #empty 槽；没给槽时退回 `empty` 那句话', () => {
    const withSlot = mount({ state: 'empty' }, { empty: '<div class="emp">暂无项目</div>' })
    expect(withSlot.getByText('暂无项目')).toBeTruthy()

    const text = mount({ state: 'empty', empty: '暂无一笔' })
    const none = text.container.querySelector('.agrid__none')
    expect(none!.textContent).toBe('暂无一笔')
    expect(none!.getAttribute('colspan')).toBe(String(COLS.length))
  })

  it('状态行是平铺行（`data-card="flat"`）：卡片模式下它不该被画成一张卡', () => {
    const { container } = mount({ state: 'error' }, { error: '读不到' })
    const row = container.querySelector('tbody tr')!
    expect(row.getAttribute('data-card')).toBe('flat')
  })

  it('cards 是显式选入：不传就还是那张定宽的表', () => {
    expect(mount().container.querySelector('.agrid')!.className).not.toContain('agrid--cards')
    const cards = mount({ cards: true })
    expect(cards.container.querySelector('.agrid')!.className).toContain('agrid--cards')
    // 卡片模式照旧画真行 —— 换形态的是 CSS（容器查询），不是模板。
    expect(cards.container.querySelector('table')).not.toBeNull()
  })

  it('卡片模式的触发条件是容器宽度，不是视口', () => {
    const src = readFileSync(join(here, '../base/BaseTable.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    // 这一段和上面那条「滚动容器是自己」一样，jsdom 里量不到（没有布局引擎、
    // 也不解析容器查询），只能钉源码。
    expect(src).toContain('container: agrid / inline-size')
    expect(src).toContain('@container agrid (max-width: 700px)')
    // 定宽表格那两条几何必须在卡片模式下让位，否则 390px 上照样横着滚。
    const block = src.slice(src.indexOf('@container agrid'))
    expect(block).toContain('.agrid--cards .agrid__table')
    expect(block).toContain('min-width: 0')
    expect(block).toContain('display: none')
    // 列名改由格子自己画：页面写 `data-label`，壳用 `content: attr(...)` 画出来。
    expect(block).toContain('content: attr(data-label)')
    expect(block).toContain("td[data-card='hide']")
  })

  it('真行的几何按结构选（`:deep`），不按「页面得记得加的那个类」选', () => {
    const src = readFileSync(join(here, '../base/BaseTable.vue'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    const rule = (selector: string) => {
      const at = src.indexOf(`${selector} {`)
      if (at < 0) throw new Error(`BaseTable.vue 里找不到 ${selector}`)
      return src.slice(at, src.indexOf('}', at))
    }

    // 真行是**页面**的模板画的（表壳只提供槽），它们带的 scope 属性是页面的，
    // 所以 `.agrid__cell[data-v-表壳]` 一条都匹配不到 —— 只有表壳自己画的骨架行
    // 匹配得到。第一版就是按类选的，症状是**骨架有内边距、真行没有**：数据到货那
    // 一刻整张表重排，骨架存在的唯一理由当场作废。而且 jsdom 没有布局引擎，
    // 这件事在单测里量不出来，只能这样钉源码。**别改回按类选。**
    expect(rule('.agrid__body :deep(td)')).toContain('padding: 12px 16px')
    expect(rule('.agrid__body :deep(td)')).toContain('border-bottom')
    expect(rule('.agrid__body :deep(tr:hover)')).toContain('background: var(--fill)')
    expect(src).not.toContain('agrid__cell')
  })
})
