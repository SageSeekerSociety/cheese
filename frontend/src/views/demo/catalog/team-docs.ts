/**
 * 团队文档那一册（#3774）：团队「文档」那一页的列表，和整页打开的一份团队文档。
 * 两件都只吃 props：取数、新建、删除、移动都在同目录的容器页里。
 */
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { PanelDocWiring } from '@/composables/usePanelDocWiring'
import type { ProjectMemberRow } from '@/cx_types'
import type { CatalogEntry, CatalogNeed } from './types'

import { docSession } from '../catalogFixtures'
import { docBundle, docPeopleBundle, docThreadsBundle } from '../catalogPanelsFixtures'

import { listedDocument } from '@/test/fakeLibrary'
import DocumentsView from '@/views/teams/detail/DocumentsView.vue'
import TeamDocumentView from '@/views/teams/TeamDocumentView.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n', 'router']

const ROSTER: ProjectMemberRow[] = [
  { user_handle: 'alice', name: '张华', name_source: 'human' },
  { user_handle: 'liming', name: '李明', name_source: 'human' },
]

const ROWS: LibraryRow[] = [
  {
    key: 'd1',
    rank: '1',
    doc: listedDocument({
      id: 'd1',
      team_id: 2,
      project_id: null,
      kind: 'doc',
      title: '实验室新人须知',
      doc_version: 4,
      author: 'liming',
      created_at: '2026-10-10T09:00:00Z',
      updated_at: '2026-10-17T15:00:00Z',
    }),
  },
  {
    key: 'd2',
    rank: '2',
    doc: listedDocument({
      id: 'd2',
      team_id: 2,
      project_id: null,
      kind: 'doc',
      title: '组会轮值表',
      doc_version: 2,
      author: 'alice',
      created_at: '2026-10-08T09:00:00Z',
      updated_at: '2026-10-12T10:00:00Z',
    }),
  },
]

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
    id: 'team-documents-view',
    title: 'DocumentsView · 团队的文档',
    about: '团队「文档」那一页：只属于团队、不属于哪个项目的文档，右上角「新建文档」。',
    file: 'src/views/teams/detail/DocumentsView.vue',
    component: DocumentsView,
    needs: UI,
    args: { rows: ROWS, members: ROSTER, loading: false, making: false, error: '' },
    states: [
      {
        name: '有文档',
        note: '行上写谁什么时候改的；点一行整页打开。',
        props: {},
        expect: '实验室新人须知',
      },
      {
        name: '没有文档',
        note: '还没有团队自己的文档。',
        props: { rows: [] },
        expect: '暂无文档',
      },
    ],
  },
  {
    id: 'team-document-view',
    title: 'TeamDocumentView · 团队的一份文档',
    about: '整页打开的一份团队文档：页头写「某团队的文档 / 标题」，文档顶栏在页头右边；没有芝士。',
    file: 'src/views/teams/TeamDocumentView.vue',
    component: TeamDocumentView,
    needs: UI,
    teleport: true,
    args: {
      document: { id: 'd1', projectId: '', title: '实验室新人须知' },
      wiring: wiring('第一周：领工位、开通服务器账号。'),
      error: '',
      parent: { label: '视觉课题组的文档', to: { path: '/teams/vision/docs' } },
      moving: false,
      move: NO_MOVE,
      sharing: false,
      share: { title: '', loading: false, mayShare: false, url: null, busy: false, error: '' },
    },
    states: [
      {
        name: '打开一份',
        note: '和资料库里打开一份是同一个面板，多人同时编辑、评论、修改记录都一样。',
        props: {},
        expect: '第一周：领工位',
      },
    ],
  },
]
