// 目录页：一组一张卡，卡上写清「它是什么、在哪儿、独立渲染要哪几样」。
//
// 那一句「找到几个」是给读屏软件听的一格，一直在页面上（中途插进来的 live region
// 常常不念），所以它和卡片是分开断言的两件事。
import type { CatalogEntry } from './catalog'
import type { CatalogGroup } from './demoCatalogStudio'

import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogIndexPage from './CatalogIndexPage.vue'

// 这一件和它底下的缩略图只用到 `stateProps`，不去拉整份注册表。
vi.mock('./catalog', () => ({
  stateProps: (entry: { args?: Record<string, unknown> }, state: { props?: Record<string, unknown> }) => ({
    ...entry.args,
    ...state.props,
  }),
}))

function item(id: string, title: string, file: string, needs: CatalogEntry['needs'] = []): CatalogEntry {
  return {
    id,
    title,
    about: `${title} 是干什么的`,
    file,
    needs,
    states: [{ name: '默认', note: '就这一格', props: {} }],
    component: { template: '<i class="probe">预览</i>' },
  }
}

const GROUPS: CatalogGroup[] = [
  {
    name: 'components/common',
    items: [item('user-ref', 'UserRef', 'src/components/common/UserRef.vue', ['vuetify', 'router'])],
  },
  { name: 'components/panels', items: [item('accept-card', 'AcceptCard', 'src/components/panels/AcceptCard.vue')] },
]

beforeEach(() => {
  // 替身不回调：这一份不看缩略图挂没挂（那是 CatalogThumb.spec.ts 的事）。
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  )
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('目录页', () => {
  it('每一组一段：组名和它底下有几条', () => {
    const view = render(CatalogIndexPage, { props: { groups: GROUPS, note: '' } })
    const heads = Array.from(view.container.querySelectorAll('.catalog-group-name'))
    expect(heads.map((h) => h.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
      'components/common 1',
      'components/panels 1',
    ])
    expect(view.container.querySelector('.catalog-name')?.textContent).toBe('组件目录')
  })

  it('每一张卡指到它自己那一页，卡上写清名字、说明、源码和要装哪几样', () => {
    const view = render(CatalogIndexPage, { props: { groups: GROUPS, note: '' } })
    const cards = Array.from(view.container.querySelectorAll('a.catalog-card'))
    expect(cards.map((a) => a.getAttribute('href'))).toEqual(['/demo/catalog/user-ref', '/demo/catalog/accept-card'])
    const first = cards[0].textContent ?? ''
    expect(first).toContain('UserRef')
    expect(first).toContain('UserRef 是干什么的')
    expect(first).toContain('src/components/common/UserRef.vue')
    expect(first).toContain('Vuetify · 路由')
    expect(first).toContain('1 格')
    // 一件都不用装的那一张说清楚，不是留一片空白。
    expect(cards[1].textContent).toContain('一件都不用装')
  })

  it('搜索那一句在页面上等着，卡片先到先算', () => {
    const view = render(CatalogIndexPage, { props: { groups: GROUPS, note: '找到 2 个组件' } })
    expect(view.getByRole('status').textContent).toBe('找到 2 个组件')
  })

  it('一条都对不上时那句话说清楚，不是一块空白', () => {
    const empty = render(CatalogIndexPage, { props: { groups: [], note: '没有对得上的组件。' } })
    expect(empty.container.querySelectorAll('.catalog-cell').length).toBe(0)
    expect(empty.getByRole('status').textContent).toBe('没有对得上的组件。')
  })
})
