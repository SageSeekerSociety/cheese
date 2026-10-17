/**
 * 拆分的机械验收：门面出去的名字一条不多一条不少，拆进 `./` 这八册的 17 条在整份
 * 目录里的先后一个没变。
 *
 * 为什么值一条测试：这次改动的全部主张就是「顺序和内容一个没动」。手工核一遍只能
 * 证明今天这一次；下一次有人在一册里换了顺序、把一条挪进别的册子、或者从门面顺手
 * 多导出一个名字，别的测试都不会红 —— `catalog.spec.ts` 挂的是条目本身，它不管这些
 * 条目从哪一册来、按什么顺序排。
 */
import type { CatalogEntry } from './types'

import { describe, expect, it } from 'vitest'

import * as facade from '../catalog'

import { ACCEPT_BAR_ENTRIES } from './accept-bar'
import { ACCOUNT_ENTRIES } from './account'
import { ANALYTICS_ENTRIES } from './analytics'
import { COMMON_ENTRIES } from './common'
import { CONVERSATION_ENTRIES } from './conversation'
import { NAVIGATION_ENTRIES } from './navigation'
import { PANEL_TABS_ENTRIES } from './panel-tabs'
import { PANEL_VIEW_ENTRIES } from './panel-views'

/** 拆到 `catalog/` 下面的那八册，按它们展开进 `CATALOG` 的顺序。 */
const SHARDS: readonly [string, CatalogEntry[]][] = [
  ['common', COMMON_ENTRIES],
  ['conversation', CONVERSATION_ENTRIES],
  ['accept-bar', ACCEPT_BAR_ENTRIES],
  ['panel-tabs', PANEL_TABS_ENTRIES],
  ['navigation', NAVIGATION_ENTRIES],
  ['analytics', ANALYTICS_ENTRIES],
  ['account', ACCOUNT_ENTRIES],
  ['panel-views', PANEL_VIEW_ENTRIES],
]

/** 这 17 条原来内联在 `catalog.ts` 里，拆出去之后一条都不能少、也不能换地方。 */
const SPLIT_IDS = [
  'user-ref',
  'nav-link',
  'room-message',
  'room-notice',
  'accept-card',
  'panel-tabs',
  'app-page',
  'mobile-action-sheet',
  'bottom-app-bar',
  'screen-header-title',
  'analytics-tabs',
  'learning-quote',
  'legal-links',
  'feedback-card',
  'panel-changes',
  'panel-preview',
  'panel-doc',
]

describe('catalog 门面', () => {
  it('从这里出去的值就这四个', () => {
    // 类型（`CatalogEntry` 那三条）在运行时不存在，这里说的是值的面：拆之前这个文件
    // 也只导出这四个值。
    expect(Object.keys(facade).sort()).toEqual(['CATALOG', 'catalogEntry', 'catalogGroup', 'stateProps'])
  })

  it('拆出去的是同样那 17 条，一条不多一条不少', () => {
    expect(SHARDS.flatMap(([, entries]) => entries.map((entry) => entry.id))).toEqual(SPLIT_IDS)
  })

  it('每一册在目录里整册连着排，中间没插进别的', () => {
    // 这一条只钉「连着」：`want` 就是从目录展开的那个数组里取的，册内的先后它验不了 ——
    // 那一半由下面一条的 `SPLIT_IDS` 管。
    const ids = facade.CATALOG.map((entry) => entry.id)
    for (const [name, entries] of SHARDS) {
      const want = entries.map((entry) => entry.id)
      const at = ids.indexOf(want[0])
      expect(at, `catalog/${name}.ts 的第一条不在 CATALOG 里`).toBeGreaterThanOrEqual(0)
      expect(ids.slice(at, at + want.length), `catalog/${name}.ts 那一册没连着排`).toEqual(want)
    }
  })

  it('拆出去那 17 条彼此的先后没动', () => {
    // 上面那一圈看不出两册对调了位置，也看不出册内被换过顺序：`CATALOG` 里只留拆出去
    // 那 17 条，剩下的先后必须还是从 `common` 到 `panel-views` 那一串。少了这一条，
    // 把 `...COMMON_ENTRIES` 和 `...CONVERSATION_ENTRIES` 换个个儿，其余每一条都还绿。
    //
    // 它钉的是这 17 条**彼此**的先后。整段落在目录的哪个位置是门面里那段展开顺序自己
    // 的事，这里不复述；某一册挪到没拆的那二十几个 `catalog*.ts` 中间，相对次序没变，
    // 这一条不红，也不再另设一条去钉 —— 位置没有第二处权威可对。
    const ids = facade.CATALOG.map((entry) => entry.id)
    expect(
      ids.filter((id) => SPLIT_IDS.includes(id)),
      '拆出去那 17 条的先后变了'
    ).toEqual(SPLIT_IDS)
  })

  it('目录里没有重复的 id', () => {
    const ids = facade.CATALOG.map((entry) => entry.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
