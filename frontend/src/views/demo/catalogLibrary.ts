/**
 * 资料库页拆出来的四件在预览站里的条目：所在位置那一行、那一列的行、打开的那一份、
 * 起名字的对话框。
 *
 * 单独一份，是因为 `catalog.ts` 已经顶着 `frontend/src`
 * 那一千行的上限。四件都只吃 props：取数、挪动、上传都在 `ProjectLibraryView.vue`
 * 和 `composables/useLibraryNaming.ts` 里，行上那点事（点开、下载、替换、改名、
 * 版本、删除）也都报回页里，所以这里不用装假后端。
 */
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { ProjectMemberRow } from '@/cx_types'
import type { LibraryFile } from '@/lib/libraryApi'
import type { CatalogEntry, CatalogNeed } from './catalog'

import DocAuthors from '@/components/library/DocAuthors.vue'
import { libraryFile, listedDocument } from '@/test/fakeLibrary'
import LibraryCrumbs from '@/views/library/LibraryCrumbs.vue'
import LibraryFileDetail from '@/views/library/LibraryFileDetail.vue'
import LibraryNameDialog from '@/views/library/LibraryNameDialog.vue'
import LibraryRows from '@/views/library/LibraryRows.vue'
import LibraryVersionsDialog from '@/views/library/LibraryVersionsDialog.vue'
import TeamLibraryView from '@/views/teams/detail/LibraryView.vue'

const UI: CatalogNeed[] = ['vuetify']

const FILE: LibraryFile = {
  type: 'file',
  rank: '1',
  path: '合同/2026/报价说明.md',
  bytes: 2048,
  modified: 1_790_000_000,
  added_by: 'alice',
  added_at: '2026-09-20T10:00:00Z',
  room: { id: 'room-1', title: '采购' },
  replaced: 1,
  references: 3,
}

const readMarkdown = () => Promise.resolve(new TextEncoder().encode('# 报价说明\n\n第一版报价。\n').buffer)

/** 那一列里三样东西各一行：一个文件夹、一份文档（队友改的）、一份文件。 */
const ROWS: LibraryRow[] = [
  {
    key: 'dir:合同',
    rank: '1',
    folder: { type: 'folder', rank: '1', path: '合同', name: '合同', count: 4, modified: 1_790_003_600 },
  },
  {
    key: 'doc:d1',
    rank: '2',
    doc: listedDocument({
      id: 'd1',
      team_id: 1,
      project_id: 'p1',
      kind: 'doc',
      title: '2026 采购口径',
      doc_version: 3,
      author: 'cheese',
      created_by: 'alice',
      drafted_by: 'cheese',
      created_at: '2026-09-18T09:00:00Z',
      updated_at: '2026-09-21T10:00:00Z',
    }),
  },
  { key: 'file:合同/2026/报价说明.md', rank: '3', file: libraryFile('合同/2026/报价说明.md', { ...FILE }) },
]

/** 文档那一行写「谁改的」要把 handle 换成名字，所以要一份名册和一个队友名。 */
const ROSTER: ProjectMemberRow[] = [{ user_handle: 'alice', name: '爱丽丝', name_source: 'human' }]

export const LIBRARY_ENTRIES: CatalogEntry[] = [
  {
    id: 'library-crumbs',
    title: 'LibraryCrumbs',
    about: '资料库里现在在哪一层：从最上层到这一层，点哪一级回到哪一级。',
    file: 'src/views/library/LibraryCrumbs.vue',
    component: LibraryCrumbs,
    needs: UI,
    states: [
      {
        name: '两层深',
        note: '最后一级是这一层，不可点；前面每一级都报 `go`，带着那一级的整条路径，最上层是空串。',
        props: { dir: '合同/2026', root: '资料库' },
        expect: '2026',
      },
    ],
  },
  {
    id: 'library-rows',
    title: 'LibraryRows',
    about:
      '资料库里那一列的行：一个文件夹、一份文档、一份文件各长什么样，每行右边那个 ⋯ 里有什么。点名字是打开，右键弹的是同一个菜单。',
    file: 'src/views/library/LibraryRows.vue',
    component: LibraryRows,
    needs: UI,
    args: {
      rows: ROWS,
      // 这是列自己的滚动容器，在页面里由页面给；预览站里没有这一层，滚回到列表自己身上。
      scroller: null,
      selectedPath: '',
      busy: '',
      browsing: true,
      members: ROSTER,
      agentName: '芝士',
    },
    states: [
      {
        name: '一层一层看',
        note: '这一层里三样东西各一行，名字只写最后一层；正开着的那一份底色深一点（`selectedPath`），文档那行写的是队友名不是 handle。',
        props: { selectedPath: '合同/2026/报价说明.md' },
        expect: '报价说明.md',
      },
      {
        name: '搜出来的',
        note: '平铺时名字写完整路径，才能看出它本来在哪一层；这一行正忙（`busy`）时 ⋯ 里那颗「删除」转着圈。',
        props: { browsing: false, busy: '合同/2026/报价说明.md' },
        expect: '合同/2026/报价说明.md',
      },
    ],
  },
  {
    id: 'doc-authors',
    title: 'DocAuthors',
    about:
      '一份文档的作者：并列的头像和名字，像 git 提交的 co-author。建成时定下，之后谁再改都不变。资料库每一行和文档页标题下面都是它。',
    file: 'src/components/library/DocAuthors.vue',
    component: DocAuthors,
    needs: UI,
    args: {
      authors: [
        { handle: 'alice', name: '爱丽丝', avatar: null, agent: false },
        { handle: 'cheese', name: '芝士', avatar: null, agent: true },
      ],
    },
    states: [
      {
        name: '让芝士写的',
        note: '让它写的人在前，芝士在后，中间只靠间距分开。',
        props: {},
        expect: '爱丽丝',
      },
      {
        name: '人自己建的',
        note: '只有这个人。',
        props: { authors: [{ handle: 'alice', name: '爱丽丝', avatar: null, agent: false }] },
        expect: '爱丽丝',
      },
    ],
  },
  {
    id: 'library-file-detail',
    title: 'LibraryFileDetail',
    about: '打开的那一份：谁在哪给的、被引用和替换过几次、里面写了什么。',
    file: 'src/views/library/LibraryFileDetail.vue',
    component: LibraryFileDetail,
    needs: [...UI, 'router'],
    states: [
      {
        name: '桌面右栏',
        note: '标题是完整路径（文件夹也在里面），下面一行是来源，再下面是引用和替换的次数；下载、替换、关闭都只往上报。',
        props: { shelf: 'p1', file: FILE, wide: true, revision: 0, busy: false, read: readMarkdown },
        expect: '合同/2026/报价说明.md',
      },
    ],
  },
  {
    id: 'library-name-dialog',
    title: 'LibraryNameDialog',
    about: '给资料库里的一份文件或一个文件夹起名字：「移动或重命名」和「新建文件夹」共用。',
    file: 'src/views/library/LibraryNameDialog.vue',
    component: LibraryNameDialog,
    needs: UI,
    teleport: true,
    states: [
      {
        name: '移动或重命名',
        note: '名字就是位置，所以分成「放在哪个文件夹」和「叫什么」两格；提交时拼回一条路径报上去（`submit`）。',
        props: {
          modelValue: true,
          title: '移动或重命名',
          primaryLabel: '移动',
          name: '报价说明.md',
          folder: '合同/2026',
          folders: ['合同', '合同/2026', '归档'],
        },
        expect: '移动或重命名',
      },
      {
        name: '新建文件夹',
        note: '不给 `folders` 就只有名字那一格：新文件夹放在打开它的那一层。',
        props: { modelValue: true, title: '新建文件夹', primaryLabel: '新建' },
        expect: '新建文件夹',
      },
      {
        name: '名字被占了',
        note: '服务端拒了（同名的已经在那儿）：那句原因挂在名字那一格底下，框不关。',
        props: {
          modelValue: true,
          title: '移动或重命名',
          primaryLabel: '移动',
          name: '报价说明.md',
          folder: '归档',
          folders: ['合同', '归档'],
          error: '资料库里已经有《归档/报价说明.md》了',
        },
        expect: '资料库里已经有《归档/报价说明.md》了',
      },
    ],
  },
  {
    id: 'library-versions-dialog',
    title: 'LibraryVersionsDialog',
    about: '一份资料的每一版：下载哪一版、把旧版恢复成当前都只往上报，请求在页面里（`useLibraryVersions`）。',
    file: 'src/views/library/LibraryVersionsDialog.vue',
    component: LibraryVersionsDialog,
    needs: UI,
    teleport: true,
    states: [
      {
        name: '两版',
        note: '新的在上，当前那一版没有「恢复」。',
        props: {
          path: '预算.xlsx',
          versions: [
            { id: 'r2', version: 2, bytes: 4096, added_by: 'bob', added_at: '2026-09-21T10:00:00Z', current: true },
            { id: 'r1', version: 1, bytes: 3072, added_by: 'alice', added_at: '2026-09-20T10:00:00Z', current: false },
          ],
          loading: false,
          failed: null,
          actionError: '',
          restoring: null,
          canRestore: true,
          fmtBytes: (n: number) => `${Math.round(n / 1024)} KB`,
        },
        expect: '第 2 版',
      },
    ],
  },
  {
    id: 'team-library-view',
    title: 'teams/detail/LibraryView',
    about:
      '团队的资料库，一个人名下的就是自己的：团队自己的文档和文件在一张表里，带用量；取数和写操作都在 `Library.vue`。',
    file: 'src/views/teams/detail/LibraryView.vue',
    component: TeamLibraryView,
    needs: UI,
    states: [
      {
        name: '自己的资料库',
        note: '开头那句说这里放什么；下面一条是用量。文档和文件按改动时间混排。',
        props: {
          team: 1,
          personal: true,
          room: { used: 186 * 1024 * 1024, total: 1024 * 1024 * 1024 },
          rows: [
            {
              key: 'doc:d2',
              rank: '1',
              doc: listedDocument({
                id: 'd2',
                team_id: 1,
                project_id: null,
                kind: 'doc',
                title: '读书笔记',
                doc_version: 2,
                author: 'alice',
                created_by: 'alice',
                created_at: '2026-10-20T09:00:00Z',
                updated_at: '2026-10-22T10:00:00Z',
              }),
            },
            { key: 'file:成绩分布.png', rank: '2', file: { ...FILE, path: '成绩分布.png', room: null } },
          ],
          members: ROSTER,
          confirmingDoc: null,
          loading: false,
          loadFailed: false,
          loadReason: '',
          actionError: '',
          dir: '',
          browsing: true,
          anything: true,
          selected: null,
          selectedPath: '',
          busy: '',
          wide: true,
          revision: 0,
          read: readMarkdown,
          versionsOf: null,
          versions: [],
          versionsLoading: false,
          versionsFailed: null,
          versionsError: '',
          restoring: null,
          replacing: null,
          confirming: null,
          naming: false,
          namingDialog: { title: '新建文件夹', primaryLabel: '新建' },
          namingBusy: false,
          namingError: '',
          query: '',
          kind: 'all',
        },
        expect: '成绩分布.png',
      },
    ],
  },
]
