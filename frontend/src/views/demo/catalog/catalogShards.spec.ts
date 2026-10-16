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

  it('每一册整册连着排在目录里，册内顺序没动', () => {
    const ids = facade.CATALOG.map((entry) => entry.id)
    for (const [name, entries] of SHARDS) {
      const want = entries.map((entry) => entry.id)
      const at = ids.indexOf(want[0])
      expect(at, `catalog/${name}.ts 的第一条不在 CATALOG 里`).toBeGreaterThanOrEqual(0)
      expect(ids.slice(at, at + want.length), `catalog/${name}.ts 那一册没连着排`).toEqual(want)
    }
  })

  it('目录里没有重复的 id', () => {
    const ids = facade.CATALOG.map((entry) => entry.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
