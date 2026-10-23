/**
 * 团队那一册：整页打开的一份团队文档（#3774；列表在团队的资料库里，`catalogLibrary.ts`），
 * 和团队设置那一层。都只吃 props：取数、删除、移动、换栏都在容器页里。
 */
import type { PanelDocWiring } from '@/composables/usePanelDocWiring'
import type { CatalogEntry, CatalogNeed } from './types'

import { docSession } from '../catalogFixtures'
import { docBundle, docPeopleBundle, docThreadsBundle } from '../catalogPanelsFixtures'

import { t } from '@/i18n'
import TeamDocumentView from '@/views/teams/TeamDocumentView.vue'
import TeamSettingsView from '@/views/teams/TeamSettingsView.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n', 'router']

function wiring(markdown: string): PanelDocWiring {
  return {
    docPanel: docBundle({ session: docSession(markdown) }),
    docThreads: docThreadsBundle(),
    docPeople: docPeopleBundle(),
  }
}

const NO_MOVE = {
  title: '',
  places: [],
  current: { teamId: 2, projectId: null },
  saving: false,
  error: '',
}

export const TEAM_DOCS_ENTRIES: CatalogEntry[] = [
  {
    id: 'team-document-view',
    title: 'TeamDocumentView · 团队的一份文档',
    about: '整页打开的一份团队文档：页头写「某团队的资料库 / 标题」，文档顶栏在页头右边；没有芝士。',
    file: 'src/views/teams/TeamDocumentView.vue',
    component: TeamDocumentView,
    needs: UI,
    teleport: true,
    args: {
      document: { id: 'd1', projectId: '', title: '实验室新人须知' },
      authors: [
        { handle: 'liming', name: '李明', avatar: null, agent: false },
        { handle: 'cheese', name: '芝士', avatar: null, agent: true },
      ],
      wiring: wiring('第一周：领工位、开通服务器账号。'),
      error: '',
      parent: { label: '视觉课题组的资料库', to: { path: '/teams/vision/library' } },
      moving: false,
      move: NO_MOVE,
      sharing: false,
      share: { title: '', loading: false, mayShare: false, url: null, busy: false, error: '' },
    },
    states: [
      {
        name: '打开一份',
        note: '和资料库里打开一份是同一个面板，多人同时编辑、评论、修改记录都一样。标题下面是作者。',
        props: {},
        expect: '第一周：领工位',
      },
    ],
  },
  {
    id: 'team-settings-view',
    title: 'TeamSettingsView · 团队设置',
    about: '团队设置那一层：和个人、空间、项目的设置同一个外框，左边成员、设备、模型、额度；自己名下没有成员。',
    file: 'src/views/teams/TeamSettingsView.vue',
    component: TeamSettingsView,
    needs: UI,
    teleport: true,
    args: {
      name: '视觉课题组',
      handle: 'vision',
      avatar: '',
      personal: false,
      groups: [
        {
          key: 'team',
          items: [
            { key: 'TeamsDetailMembers', label: t('home.nav.teamMembers'), to: { path: '/teams/vision/members' } },
            { key: 'TeamsDetailCompute', label: t('home.nav.teamCompute'), to: { path: '/teams/vision/compute' } },
            { key: 'TeamsDetailModels', label: t('home.nav.teamModels'), to: { path: '/teams/vision/models' } },
            { key: 'TeamsDetailCredits', label: t('home.nav.teamCredits'), to: { path: '/teams/vision/credits' } },
          ],
        },
      ],
      active: 'TeamsDetailMembers',
      title: t('home.nav.teamMembers'),
      backTo: { path: '/teams/vision/settings' },
      actions: [],
    },
    states: [
      {
        name: '团队',
        note: '停在「成员」：目录里这一项高亮，右边标题是这一栏的名字。',
        props: {},
        expectSelector: '.so__item.so__item--active',
      },
    ],
  },
]
