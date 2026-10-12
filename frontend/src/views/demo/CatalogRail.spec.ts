// 常驻左栏：站自己那两页在最上面一直开着，底下的组件按目录收起来，当前这一条那一组
// 默认展开。
//
// 要守的三件事：收起来的那几组**不在 DOM 里**（整份目录 273 条，`v-show` 也会把它们
// 全建出来）、当前这一条那一组一进页面就是开的、那两页在窄屏下是唯一的入口所以一直
// 在（顶栏那几个入口在窄屏收掉了）。
import type { CatalogEntry } from './catalog'

import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import CatalogRail from './CatalogRail.vue'
import { STUDIO_PAGES } from './demoCatalogStudio'

function item(id: string, title: string): CatalogEntry {
  return { id, title, about: `${title} 的说明`, file: `src/${id}.vue`, needs: [], states: [], component: {} }
}

/** 站自己那两页在组件目录上面，所以「第一条组件链路」不是第 0 条。 */
const PAGES = STUDIO_PAGES.length

const GROUPS = [
  { name: 'components/common', items: [item('user-ref', 'UserRef'), item('nav-link', 'NavLink')] },
  { name: 'components/panels', items: [item('accept-card', 'AcceptCard')] },
]

describe('左栏', () => {
  it('一进来每一组都是收着的：整份目录不在 DOM 里', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS } })
    expect(view.container.querySelectorAll('.rail-link').length).toBe(PAGES)
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
    const links = view.container.querySelectorAll('.rail-group .rail-link')
    expect(Array.from(links).map((a) => a.getAttribute('href'))).toEqual([
      '/demo/catalog/user-ref',
      '/demo/catalog/nav-link',
    ])
    await fireEvent.click(head)
    expect(head.getAttribute('aria-expanded')).toBe('false')
    expect(view.container.querySelectorAll('.rail-group .rail-link').length).toBe(0)
  })

  it('站在某一条上时，它那一组是开的，那一条自己标出来', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS, current: 'nav-link' } })
    const links = Array.from(view.container.querySelectorAll('.rail-group .rail-link'))
    expect(links.length).toBe(2)
    const mine = links.find((a) => a.getAttribute('href') === '/demo/catalog/nav-link')!
    expect(mine.getAttribute('aria-current')).toBe('page')
    expect(
      links.find((a) => a.getAttribute('href') === '/demo/catalog/user-ref')!.getAttribute('aria-current')
    ).toBeNull()
    // 别的那一组还是收着的。
    expect(view.container.querySelectorAll('.rail-head')[1].getAttribute('aria-expanded')).toBe('false')
  })

  it('站自己那两页一直开着：窄屏上顶栏那排入口收掉了，这里是唯一的入口', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS } })
    const pages = Array.from(view.container.querySelectorAll('.rail-link-page'))
    expect(pages.map((a) => a.getAttribute('href'))).toEqual(STUDIO_PAGES.map((page) => `/demo/catalog/${page.id}`))
    expect(pages.map((a) => a.textContent)).toEqual(STUDIO_PAGES.map((page) => page.label))
  })

  it('站在这两页上时那一条自己标出来', () => {
    const view = render(CatalogRail, { props: { groups: GROUPS, current: 'tokens' } })
    const mine = view.container.querySelector('.rail-link-page[href="/demo/catalog/tokens"]')!
    expect(mine.getAttribute('aria-current')).toBe('page')
    expect(
      view.container.querySelector('.rail-link-page[href="/demo/catalog/motion"]')?.getAttribute('aria-current')
    ).toBeNull()
  })
})
