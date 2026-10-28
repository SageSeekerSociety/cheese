/**
 * 文档分享那一册（#3774）：文档「⋯」里「分享…」的框，和拿到链接的人打开的那一页。
 * 两件都只吃 props：链接的取、建、停在页面里，分享页的取数在它的容器里。
 */
import type { CatalogEntry, CatalogNeed } from './types'

import DocShareDialog from '@/components/library/DocShareDialog.vue'
import SharedDocumentView from '@/views/shared/SharedDocumentView.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n']

export const DOC_SHARE_ENTRIES: CatalogEntry[] = [
  {
    id: 'library-doc-share-dialog',
    title: 'DocShareDialog · 分享…',
    about: '文档「⋯」里的「分享…」：创建一条只读链接；拿到链接的人不用登录就能读当前版本。停止分享后链接立即失效。',
    file: 'src/components/library/DocShareDialog.vue',
    component: DocShareDialog,
    needs: UI,
    teleport: true,
    args: {
      modelValue: true,
      title: '组会纪要',
      loading: false,
      mayShare: true,
      url: null,
      busy: false,
      error: '',
    },
    states: [
      {
        name: '还没有链接',
        note: '说清拿到链接的人能看到什么、看不到什么；主操作是「创建链接」。',
        props: {},
        expect: '创建链接',
      },
      {
        name: '已经分享',
        note: '链接在框里，主操作变成「复制链接」，旁边是「停止分享」。',
        props: { url: 'https://okcheese.com/s/3m9Qx2', busy: false },
        expect: '停止分享',
      },
      {
        name: '不能分享',
        note: '角色没有「分享文档」权限的人：只写一句谁能分享，没有按钮。',
        props: { mayShare: false },
        expect: '能分享',
      },
    ],
  },
  {
    id: 'shared-document-view',
    title: 'SharedDocumentView · 分享页',
    about: '拿到链接的人打开的那一页：标题、更新时间、当前版本的正文；不套应用外壳，不用登录。',
    file: 'src/views/shared/SharedDocumentView.vue',
    component: SharedDocumentView,
    needs: UI,
    args: {
      doc: {
        title: '组会纪要',
        content: '## 本周进展\n\n- 数据清洗完成\n- 下周开始标注\n',
        updated_at: '2026-10-17T10:00:00Z',
        shared_by: { handle: 'alice', nickname: '爱丽丝', code: 'K7Q2M9', seats_left: 0 },
      },
      error: '',
    },
    states: [
      {
        name: '一份文档',
        note: '正文和文档里读起来一样；分享人的名额用完时，页脚只指向知是首页。',
        props: {},
        expect: '数据清洗完成',
      },
      {
        name: '分享人还有受邀名额',
        note: '页脚左侧写明作者与芝士协作署名，右侧「认识知是 →」链接带着邀请码进注册页。',
        props: {
          doc: {
            title: '组会纪要',
            content: '## 本周进展\n\n- 数据清洗完成\n',
            updated_at: '2026-10-17T10:00:00Z',
            shared_by: { handle: 'alice', nickname: '爱丽丝', code: 'K7Q2M9', seats_left: 2 },
          },
        },
        expect: '认识知是',
      },
      {
        name: '链接已失效',
        note: '停止分享以后再打开：只有一句话。',
        props: { doc: null, error: '这条分享链接已失效' },
        expect: '已失效',
      },
    ],
  },
]
