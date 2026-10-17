/**
 * 工作面板的页签条（`PanelTabs`）那一册。
 *
 * 页签本身从产品那张表（`panelTabList`）现取，预览站不重复写一份：那张表改了，这里
 * 跟着改，没有第二份会过期的抄本。
 */
import type { CatalogEntry } from './types'

import { OPEN_FILES } from '../catalogFixtures'

import { UI } from './needs'

import { panelTabs } from '@/components/panels/panelTabList'
import PanelTabs from '@/components/panels/PanelTabs.vue'

/** 工作面板那几格（产品那张表，见 `panelTabList`），预览站不重复写一份。 */
const TABS = panelTabs(false).map((tab) => ({ key: String(tab.key), label: tab.label, icon: tab.icon }))

export const PANEL_TABS_ENTRIES: CatalogEntry[] = [
  {
    id: 'panel-tabs',
    title: 'PanelTabs',
    about: '工作面板的页签条：四格固定页签，外加读者自己打开的那几份文件。',
    file: 'src/components/panels/PanelTabs.vue',
    component: PanelTabs,
    needs: UI,
    states: [
      {
        name: '四格',
        note: '哪四格、叫什么、挂哪个图标来自产品那张表（panelTabList），这里不另写一份。',
        props: { tabs: TABS, active: 'site' },
        expect: '现场',
      },
      {
        name: '有一格是空的',
        note: '这一格此刻没东西：字退到 --faint，但照样能点。',
        props: {
          tabs: TABS.map((tab) =>
            tab.key === 'changes'
              ? { ...tab, title: '改动（还没有改动）', empty: true }
              : tab.key === 'preview'
                ? { ...tab, title: '预览（README.md）', signal: { kind: 'dot' } }
                : tab
          ),
          active: 'preview',
        },
        expect: '改动',
      },
      {
        name: '有东西等你看',
        note: '信号三档：现场在跑是呼吸点、改动是数字、预览是新内容。',
        props: {
          tabs: TABS.map((tab) =>
            tab.key === 'site'
              ? { ...tab, signal: { kind: 'pulse' } }
              : tab.key === 'changes'
                ? { ...tab, signal: { kind: 'count', count: 3 } }
                : tab
          ),
          active: 'changes',
        },
        expect: '3',
      },
      {
        name: '打开了一份文件',
        note: '自由区：读者自己打开的那几份文件各占一格，可以关、可以钉住。',
        props: { tabs: TABS, active: 'file:README.md', files: OPEN_FILES },
        expect: 'README.md',
      },
      {
        name: '手机上',
        note: '窄屏上这条栏加高、横向滚动，阶段自动选中的那一格会被带回视野里。',
        props: { tabs: TABS, active: 'site', phone: true },
        expect: '概览',
      },
    ],
  },
]
