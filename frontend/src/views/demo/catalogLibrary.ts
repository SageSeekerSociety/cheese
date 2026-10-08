/**
 * 资料库页拆出来的三件在预览站里的条目：所在位置那一行、打开的那一份、起名字的
 * 对话框。
 *
 * 单独一份的理由和 `catalogKnowledge.ts` 一样：`catalog.ts` 已经顶着 `frontend/src`
 * 那一千行的上限。三件都只吃 props：取数、挪动、上传都在 `ProjectLibraryView.vue`
 * 和 `composables/useLibraryNaming.ts` 里，打开的那一份连字节怎么读都是页给的
 * （`read`），所以这里不用装假后端。
 */
import type { LibraryFile } from '@/lib/libraryApi'
import type { CatalogEntry, CatalogNeed } from './catalog'

import LibraryCrumbs from '@/views/library/LibraryCrumbs.vue'
import LibraryFileDetail from '@/views/library/LibraryFileDetail.vue'
import LibraryNameDialog from '@/views/library/LibraryNameDialog.vue'

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
        props: { projectId: 'p1', file: FILE, wide: true, revision: 0, busy: false, read: readMarkdown },
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
]
