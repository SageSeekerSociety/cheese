/**
 * 团队那一册：整页打开的一份团队文档（#3774；列表在团队的资料库里，`catalogLibrary.ts`），
 * 和团队设置那一层。都只吃 props：取数、删除、移动、换栏都在容器页里。
 */
import type { PanelDocWiring } from '@/composables/usePanelDocWiring'
import type { Team } from '@/types'
import type { CatalogEntry, CatalogNeed } from './types'

import { docSession } from '../catalogFixtures'
import { docBundle, docPeopleBundle, docThreadsBundle } from '../catalogPanelsFixtures'

import { t } from '@/i18n'
import LeaveView from '@/views/teams/detail/LeaveView.vue'
import ProfileView from '@/views/teams/detail/ProfileView.vue'
import TeamDocumentView from '@/views/teams/TeamDocumentView.vue'
import TeamSettingsView from '@/views/teams/TeamSettingsView.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n', 'router']

const VISION = {
  id: 2,
  handle: 'vision',
  name: '视觉课题组',
  intro: '做三维重建和视频理解',
  avatarId: 0,
  owner: { id: 1 },
  admins: { total: 0, examples: [] },
  members: { total: 3, examples: [] },
  visibility: 'public',
  role: 'OWNER',
} as unknown as Team

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
    about:
      '团队设置那一层：和个人、空间、项目的设置同一个外框。左边「团队」「资源」两组，最后红字一项转让与解散（或退出）。自己名下的设备、模型、额度在个人设置里。',
    file: 'src/views/teams/TeamSettingsView.vue',
    component: TeamSettingsView,
    needs: UI,
    teleport: true,
    args: {
      name: '视觉课题组',
      handle: 'vision',
      avatar: '',
      groups: [
        {
          key: 'team',
          title: t('teams.settings.groups.team'),
          items: [
            { key: 'TeamsDetailProfile', label: t('teams.settings.profile'), to: { path: '/teams/vision/profile' } },
            { key: 'TeamsDetailMembers', label: t('home.nav.teamMembers'), to: { path: '/teams/vision/members' } },
          ],
        },
        {
          key: 'resources',
          title: t('teams.settings.groups.resources'),
          items: [
            { key: 'TeamsDetailCompute', label: t('home.nav.teamCompute'), to: { path: '/teams/vision/compute' } },
            { key: 'TeamsDetailModels', label: t('home.nav.teamModels'), to: { path: '/teams/vision/models' } },
            { key: 'TeamsDetailCredits', label: t('home.nav.teamCredits'), to: { path: '/teams/vision/credits' } },
          ],
        },
        {
          key: 'leave',
          items: [
            {
              key: 'TeamsDetailLeave',
              label: t('teams.settings.transferAndDisband'),
              to: { path: '/teams/vision/leave' },
              danger: true,
            },
          ],
        },
      ],
      active: 'TeamsDetailMembers',
      backTo: { path: '/teams/vision/settings' },
    },
    states: [
      {
        name: '团队',
        note: '停在「成员」：目录里这一项高亮。每一栏自己画标题和操作，和个人设置的各页一样。',
        props: {},
        expectSelector: '.so__item.so__item--active',
      },
    ],
  },
  {
    id: 'team-profile-view',
    title: 'teams/detail/ProfileView · 团队的基本信息',
    about: '团队设置的「基本信息」：头像、名称、团队地址、简介，改了才出现保存栏；存和换地址在 `Profile.vue`。',
    file: 'src/views/teams/detail/ProfileView.vue',
    component: ProfileView,
    needs: UI,
    args: {
      team: VISION,
      avatar: '',
      canEdit: true,
      addressPrefix: 'okcheese.com/teams/',
      avatarAccept: 'image/png',
      saving: false,
      saved: false,
      error: '',
      nameError: '',
      handleError: '',
    },
    states: [
      { name: '所有者或管理员', note: '四格都能改。', props: {}, expect: '团队地址' },
      {
        name: '普通成员',
        note: '只看不改：上面一句说只有所有者和管理员能修改，四格是灰的，没有保存栏。',
        props: { canEdit: false },
        expect: '只有团队所有者和管理员能修改资料',
      },
      {
        name: '地址被占用',
        note: '这句错落在地址那一格下面。',
        props: { handleError: '这个地址已被占用' },
        expect: '这个地址已被占用',
      },
    ],
  },
  {
    id: 'team-leave-view',
    title: 'teams/detail/LeaveView · 转让与解散',
    about: '团队设置最后那一栏：所有者转让或解散，其他成员退出；请求在 `Leave.vue`。',
    file: 'src/views/teams/detail/LeaveView.vue',
    component: LeaveView,
    needs: UI,
    teleport: true,
    args: {
      team: VISION,
      owner: true,
      candidates: [],
      candidatesLoading: false,
      busy: false,
      error: null,
      transferring: false,
      disbanding: false,
      leaving: false,
    },
    states: [
      { name: '所有者', note: '转让一行，解散一行（红字）。', props: {}, expect: '转让团队' },
      { name: '其他成员', note: '只有退出一行。', props: { owner: false }, expect: '退出团队' },
    ],
  },
]
