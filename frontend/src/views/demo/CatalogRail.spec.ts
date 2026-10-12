// 常驻左栏：几百条平铺没人找得到，所以按目录收起来，当前这一条那一组默认展开。
//
// 要守的两件事：收起来的那几组**不在 DOM 里**（整份目录 273 条，`v-show` 也会把它们
// 全建出来），以及当前这一条那一组一进页面就是开的。
import type { CatalogEntry } from './catalog'

import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import CatalogRail from './CatalogRail.vue'

function item(id: string, title: string): CatalogEntry {
  return { id, title, about: `${title} 的说明`, file: `src/${id}.vue`, needs: [], states: [], component: {} }
}

const GROUPS = [
  { name: 'components/common', items: [item('user-ref', 'UserRef'), item('nav-link', 'NavLink')] },
  { name: 'components/panels', items: [item('accept-card', 'AcceptCard')] },
]

describe('左栏', () => {
  it('一进来每一组都是收着的：整份目录不在 DOM 里', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS } })
    expect(view.container.querySelectorAll('.rail-link').length).toBe(0)
    expect(view.container.querySelectorAll('.rail-head').length).toBe(2)
    // 组名和条数一直看得见，收起来的是组里的那几条。
    expect(view.container.textContent).toContain('components/common')
    expect(view.container.textContent).toContain('2')
  })

  it('点组名展开它自己那一组，再点收起', async () => {
    const view = render(CatalogRail, { props: { groups: GROUPS } })
    const head = view.container.querySelectorAll('.rail-head')[0]
    await fireEvent.click(head)
    expect(head.getAttribute('aria-expanded')).toBe('true')
    const links = view.container.querySelectorAll('.rail-link')
    expect(Array.from(links).map((a) => a.getAttribute('href'))).toEqual([
      '/demo/catalog/user-ref',
      '/demo/catalog/nav-link',
    ])
    await fireEvent.click(head)
    expect(head.getAttribute('aria-expanded')).toBe('false')
    expect(view.container.querySelectorAll('.rail-link').length).toBe(0)
  })

  it('站在某一条上时，它那一组是开的，那一条自己标出来', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS, current: 'nav-link' } })
    const links = Array.from(view.container.querySelectorAll('.rail-link'))
    expect(links.length).toBe(2)
    const mine = links.find((a) => a.getAttribute('href') === '/demo/catalog/nav-link')!
    expect(mine.getAttribute('aria-current')).toBe('page')
    expect(
      links.find((a) => a.getAttribute('href') === '/demo/catalog/user-ref')!.getAttribute('aria-current')
    ).toBeNull()
    // 别的那一组还是收着的。
    expect(view.container.querySelectorAll('.rail-head')[1].getAttribute('aria-expanded')).toBe('false')
  })
})
