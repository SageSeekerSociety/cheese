// 站骨架的三件事：地址怎么读、有哪几档设备、目录怎么分组。
//
// 都是纯函数，不挂页面。设备那几档守的是「真 px」：桌面/平板/手机记的是真宽度，
// `跟随容器` 是唯一没有宽度的那一档，工具条按它决定用不用 iframe。
import type { CatalogEntry } from './catalog'

import { describe, expect, it, vi } from 'vitest'

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
  stageHref,
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

  it('单格那一页的地址把主题、动效、底都带上：iframe 里是另一篇文档', () => {
    expect(stageHref('user-ref', 0, { theme: 'dark', motion: 'slow', bg: 'canvas' })).toBe(
      '/demo/catalog/user-ref/frame/0?theme=dark&motion=slow&bg=canvas'
    )
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
