/** BaseTable 的排序表头、分页条和页面侧排序。 */
import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import BaseTable from './BaseTable.vue'
import BaseTableTh from './BaseTableTh.vue'
import TablePager from './TablePager.vue'
import { compareBy, useClientTable } from './tableSort'

const vuetify = createVuetify()

describe('排序表头', () => {
  function mount(sortKey: string | null, sortDir: 'asc' | 'desc' = 'asc') {
    const events: [string, string][] = []
    const Host = defineComponent(
      () => () =>
        h(
          BaseTable,
          { cols: [null, '80px'], label: 'x', sortKey, sortDir, onSort: (k: string, d: string) => events.push([k, d]) },
          {
            head: () =>
              h('tr', [h(BaseTableTh, { sortKey: 'name' }, () => '名字'), h(BaseTableTh, null, () => '备注')]),
          }
        )
    )
    return { ...render(Host, { global: { plugins: [vuetify] } }), events }
  }

  it('可排序的列是一颗按钮，点了发 sort；不可排序的列不是', async () => {
    const { container, events } = mount(null)
    const ths = container.querySelectorAll('th')
    expect(ths[0].querySelector('button')).not.toBeNull()
    expect(ths[1].querySelector('button')).toBeNull()
    expect(ths[0].getAttribute('aria-sort')).toBe('none')
    await fireEvent.click(ths[0].querySelector('button')!)
    expect(events).toEqual([['name', 'asc']])
  })

  it('当前那一列再点一次换方向，并念得出方向', async () => {
    const { container, events } = mount('name', 'asc')
    const th = container.querySelector('th')!
    expect(th.getAttribute('aria-sort')).toBe('ascending')
    await fireEvent.click(th.querySelector('button')!)
    expect(events).toEqual([['name', 'desc']])
  })
})

describe('分页条', () => {
  it('只有一页时不画', () => {
    const { container } = render(TablePager, { props: { total: 10 }, global: { plugins: [vuetify] } })
    expect(container.querySelector('nav')).toBeNull()
  })

  it('写出范围，翻页改 page', async () => {
    const page = ref(1)
    const Host = defineComponent(
      () => () => h(TablePager, { total: 23, page: page.value, 'onUpdate:page': (v: number) => (page.value = v) })
    )
    const { container } = render(Host, { global: { plugins: [vuetify] } })
    expect(container.textContent).toContain('1–10')
    const [prev, next] = container.querySelectorAll('button')
    expect(prev.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(next)
    expect(page.value).toBe(2)
    expect(container.textContent).toContain('11–20')
  })
})

describe('页面侧排序', () => {
  it('数字按大小、字符串按本地化顺序，空值不论方向都在最后', () => {
    const rows = [{ v: 3 }, { v: null }, { v: 1 }, { v: 2 }]
    expect(compareBy(rows, 'v', 'asc').map((r) => r.v)).toEqual([1, 2, 3, null])
    expect(compareBy(rows, 'v', 'desc').map((r) => r.v)).toEqual([3, 2, 1, null])
  })

  it('换了排序回到第一页，每页 10 条', () => {
    const rows = ref(Array.from({ length: 25 }, (_, i) => ({ n: i })))
    const table = useClientTable(rows)
    table.page.value = 3
    expect(table.pageRows.value).toHaveLength(5)
    table.onSort('n', 'desc')
    expect(table.page.value).toBe(1)
    expect(table.pageRows.value[0].n).toBe(24)
  })
})
