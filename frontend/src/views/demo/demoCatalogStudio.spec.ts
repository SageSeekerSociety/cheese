// 站骨架的三件事：地址怎么读、有哪几档设备、目录怎么分组。
//
// 都是纯函数，不挂页面。设备那几档守的是「真 px」：桌面/平板/手机记的是真宽度，
// `跟随容器` 是唯一没有宽度的那一档，工具条按它决定用不用 iframe。
import type { CatalogEntry } from './catalog'

import { describe, expect, it, vi } from 'vitest'

// 「有没有组件和站自己的一页撞名」得读整份注册表才问得出，所以这一份比别的纯函数
// spec 重（要把几百个组件过一遍编译）—— 那条不变量值这个价：撞了名，那一页会被一个
// 组件悄悄顶掉。夹具在模块加载时就取了词，语言得在 import 之前定下来
// （`catalog.spec.ts` 同款）；其余几组是纯函数，用不着这一下。
vi.hoisted(() => localStorage.setItem('cheese:locale', 'zh-CN'))

import { CATALOG } from './catalog'
import {
  countIn,
  defaultPrefs,
  deviceOf,
  DEVICES,
  documentTheme,
  DUAL_DEVICES,
  groupCatalog,
  matchesCatalog,
  needsOf,
  parseCatalogPath,
  reservedPage,
  stageHref,
  STUDIO_PAGES,
} from './demoCatalogStudio'

/** 分组只按这一列的四个字段走，不碰真注册表那一套。 */
function entry(id: string, title: string, about: string, file: string): CatalogEntry {
  return { id, title, about, file, needs: [], states: [], component: { template: '<i />' } }
}

const ENTRIES = [
  entry('user-ref', 'UserRef', '句子里提到的人', 'src/components/common/UserRef.vue'),
  entry('nav-link', 'NavLink', '按 name 指路', 'src/components/common/NavLink.vue'),
  entry('accept-card', 'AcceptCard', '验收卡', 'src/components/panels/AcceptCard.vue'),
]

describe('地址', () => {
  it('目录 / 单组件页 / 单格，三段读得出来', () => {
    expect(parseCatalogPath('/demo/catalog')).toEqual({ id: null, frame: null })
    expect(parseCatalogPath('/demo/catalog/user-ref')).toEqual({ id: 'user-ref', frame: null })
    expect(parseCatalogPath('/demo/catalog/user-ref/frame/2')).toEqual({ id: 'user-ref', frame: 2 })
  })

  it('别的页面上没有组件站的地址，读出来是空的', () => {
    expect(parseCatalogPath('/demo/quickstart')).toEqual({ id: null, frame: null })
    expect(parseCatalogPath('/')).toEqual({ id: null, frame: null })
  })

  it('只是开头像，不算组件站的地址', () => {
    // 前缀命中：`/demo/catalog-archive` 不是这一站的页面，不该被当成目录。
    expect(parseCatalogPath('/demo/catalog-archive')).toEqual({ id: null, frame: null })
    expect(parseCatalogPath('/demo/catalogX')).toEqual({ id: null, frame: null })
  })

  it('单格那一页的地址把主题、动效、底都带上：iframe 里是另一篇文档', () => {
    expect(stageHref('user-ref', 0, { theme: 'dark', motion: 'slow', bg: 'canvas' })).toBe(
      '/demo/catalog/user-ref/frame/0?theme=dark&motion=slow&bg=canvas'
    )
  })
})

describe('站自己那两页', () => {
  it('动效台和 token 页都在名单里，各有名字和一句话', () => {
    expect(STUDIO_PAGES.map((page) => page.id)).toEqual(['motion', 'tokens'])
    for (const page of STUDIO_PAGES) {
      expect(page.label.length, page.id).toBeGreaterThan(0)
      expect(page.about.length, page.id).toBeGreaterThan(8)
    }
  })

  it('地址上那两段认得出是站自己的页，别的段落不是', () => {
    expect(reservedPage('motion')?.label).toBe('动效台')
    expect(reservedPage('tokens')?.label).toBe('设计 token')
    // 目录本身和某个组件都不是「站自己的一页」。
    expect(reservedPage(null)).toBeNull()
    expect(reservedPage('user-ref')).toBeNull()
  })

  it('组件 id 不和这两页撞名：撞了那一页会被一个组件顶掉', () => {
    const taken = new Set(STUDIO_PAGES.map((page) => page.id))
    for (const entry of CATALOG) {
      expect(taken.has(entry.id), `${entry.id} 和站自己的一页同名`).toBe(false)
    }
  })
})

describe('设备档', () => {
  it('三档设备记的是真 px，跟随容器没有宽度', () => {
    expect(DEVICES.map((device) => device.width)).toEqual([null, 1280, 834, 390])
    expect(deviceOf('phone').width).toBe(390)
  })

  it('认不出来的档退回跟随容器，不是某一台设备', () => {
    expect(deviceOf('nope' as never).id).toBe('fit')
  })

  it('双端并排取的是宽窄差得最远的那两台', () => {
    expect(DUAL_DEVICES).toEqual(['desktop', 'phone'])
  })
})

describe('目录分组', () => {
  const byFile = (e: CatalogEntry): string => e.file.replace('src/', '').split('/').slice(0, -1).join('/')

  it('组间按目录名排、组内按标题排', () => {
    const groups = groupCatalog(ENTRIES, '', byFile)
    expect(groups.map((group) => group.name)).toEqual(['components/common', 'components/panels'])
    expect(groups[0].items.map((item) => item.title)).toEqual(['NavLink', 'UserRef'])
    expect(countIn(groups)).toBe(3)
  })

  it('搜的是标题、说明和源码路径，大小写不管', () => {
    expect(matchesCatalog(ENTRIES[0], 'userref')).toBe(true)
    expect(matchesCatalog(ENTRIES[0], 'components/common')).toBe(true)
    expect(matchesCatalog(ENTRIES[0], '句子里提到')).toBe(true)
    expect(matchesCatalog(ENTRIES[0], 'panels')).toBe(false)
    expect(groupCatalog(ENTRIES, 'PANELS', byFile).map((group) => group.name)).toEqual(['components/panels'])
  })

  it('什么都没对上就是空的，不是全份', () => {
    expect(groupCatalog(ENTRIES, '不会有组件叫这个名字', byFile)).toEqual([])
    expect(countIn([])).toBe(0)
  })

  it('「独立渲染需要」把内部名字翻译成人话，一件都不用装时说清楚', () => {
    expect(needsOf([])).toBe('一件都不用装')
    expect(needsOf(['vuetify', 'router'])).toBe('Vuetify · 路由')
  })
})

describe('开页时的默认档', () => {
  it('设备默认跟随容器：这一页本身也被文档嵌着看，套一台设备的框会撑破', () => {
    expect(defaultPrefs().device).toBe('fit')
    expect(defaultPrefs().dual).toBe(false)
  })

  it('主题跟着文档现在的那一个，不硬写浅色', () => {
    document.documentElement.dataset.theme = 'dark'
    expect(documentTheme()).toBe('dark')
    expect(defaultPrefs().theme).toBe('dark')
    delete document.documentElement.dataset.theme
    expect(defaultPrefs().theme).toBe('light')
  })

  it('系统开了「减少动态效果」时，动效默认落在关', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduced-motion') }))
    expect(defaultPrefs().motion).toBe('off')
    vi.unstubAllGlobals()
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    expect(defaultPrefs().motion).toBe('normal')
    vi.unstubAllGlobals()
  })
})
