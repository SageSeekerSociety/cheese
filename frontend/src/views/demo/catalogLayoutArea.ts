/**
 * 布局层（`components/layout-area/LayoutArea.vue`）在预览站里的条目。还没有产品页面用它，
 * 所以这里挂的是试验台 `CatalogLayoutArea.vue`：几个和业务无关的面板加一个「恢复默认摆法」。
 *
 * 每一格用自己的存储键：一格里拖乱了，不该带到另一格去。
 *
 * 规矩见 `catalog.ts`；`CatalogEntry` 是 type-only 引用，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import CatalogLayoutArea from './CatalogLayoutArea.vue'

export const LAYOUT_AREA_ENTRIES: CatalogEntry[] = [
  {
    id: 'layout-area',
    title: 'LayoutArea',
    about:
      '和业务无关的窗口管理层：格子里放页签，可拖页签分屏、拖分隔线、收起成一条、放大、锁定摆法；摆法按页面给的键和版本记在浏览器里；窄屏改成一次一格。',
    file: 'src/components/layout-area/LayoutArea.vue',
    component: CatalogLayoutArea,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '默认摆法',
        note: '左边导航钉住（关不掉、拖不走），中间对话，右边上下两格；「笔记」不在任何格里，从「+」拿回来。',
        props: { storageKey: 'catalog.layout-area.default' },
        expect: '恢复默认摆法',
      },
      {
        name: '锁定摆法',
        note: '不能拖、分屏、关页签，也没有「+」；收起、放大和拖分隔线照常。',
        props: { storageKey: 'catalog.layout-area.locked', locked: true },
        expect: '恢复默认摆法',
      },
      {
        name: '窄屏',
        note: '不挂 dockview：上面一排页签，一次一格；切过去再切回来，输入框里的字还在。',
        props: { storageKey: 'catalog.layout-area.narrow', narrow: true },
        expect: '导航 第 1 行',
      },
      {
        name: '标题栏',
        note: '右边那一格里「文件」「预览」各带自己的标题栏，一次显示一格：上面的按钮把它翻到前面，⤢ 放大，✕ 收起那一格；「预览」前面的「返回」回到「文件」。',
        props: { storageKey: 'catalog.layout-area.titled', titled: true },
        expect: '恢复默认摆法',
      },
    ],
  },
]
