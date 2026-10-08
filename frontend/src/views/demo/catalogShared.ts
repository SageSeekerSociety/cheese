/**
 * 预览站里「跨目录的共用件」那一组：表壳与其表头格、富文本编辑器和它的附件图、页头，
 * 以及句子里提到的一个人。
 *
 * 这一批的共同点是它们以前要靠一整页（或一整套 store / 路由 / 外壳）才看得见，现在各自
 * 只吃 props、或者只读一个注入口（`lib/userRefDirectory.ts`、`lib/pageChrome.ts`），没人
 * 注入时按兜底画，于是能单独摆进来。附件的图源、名册、页头的面包屑都不在预览站上，所以
 * 这里展示的正是「没有人给它们这些东西」时的样子。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用，`catalog.ts` 反过来要 `SHARED_ENTRIES` 这个值，运行时不构成循环。
 * 数据见 `catalogSharedFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { ANNOUNCEMENT_HTML, attachmentImageViewProps, EDITOR_DOC } from './catalogSharedFixtures'

import BaseTable from '@/components/base/BaseTable.vue'
import BaseTableTh from '@/components/base/BaseTableTh.vue'
import AttachmentImageView from '@/components/common/Editor/AttachmentImageView.vue'
import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import UserRefLink from '@/components/common/UserRefLink.vue'

/** 表壳和页头是 Vuetify 的图标、布局要的；表头格可排序时的那颗按钮也是。 */
const UI: CatalogNeed[] = ['vuetify']

/** 富文本编辑器还要一套语言包（工具栏那几颗按钮的标签）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

export const SHARED_ENTRIES: CatalogEntry[] = [
  {
    id: 'base-table',
    title: 'BaseTable',
    about:
      '全产品的数据表格壳：常驻表头的滚动、只有一份的列宽、和真行同高的加载骨架，三件容易写错的事各有一个地方安放。',
    file: 'src/components/base/BaseTable.vue',
    component: BaseTable,
    // 表壳自己一个 Vuetify 组件都不画：表头格、真行、分页条都是页面放进插槽的。
    needs: [],
    args: { cols: ['36%', null, '120px'], label: '反馈列表' },
    states: [
      {
        name: '首次加载：骨架行',
        note: '骨架是真的 <tr>，走同一份 colgroup、和真行一样高：数据到货那一刻整张表不重排。',
        props: { loading: true, skeletonRows: 3 },
        expectSelector: '.agrid__bone',
      },
      {
        name: '一条都没有',
        note: 'empty 给一句话就是空态：一行平铺的格子写「暂无反馈」，不是整块空白。',
        props: { empty: '暂无反馈' },
        expect: '暂无反馈',
      },
      {
        name: '读不到',
        note: 'state="error" 和空态是两句不同的话：读失败时同时说「暂无数据」是最糟的那种说不清。',
        props: { state: 'error', empty: '读取失败：请求超时' },
        expect: '读取失败：请求超时',
      },
      {
        name: '表体由页面画',
        note: '这一层只提供插槽：真行（<tr>/<td>）、表头格和表尾的分页都是页面的模板画的；这里用一段字代表页面画的那一行。',
        props: {},
        slot: '（页面画的行）',
        expect: '（页面画的行）',
      },
    ],
  },
  {
    id: 'base-table-th',
    title: 'BaseTableTh',
    about: 'BaseTable 的表头格子：给了 sortKey 就是一颗可排序的按钮（方向箭头 + aria-sort），否则是一格普通的 <th>。',
    file: 'src/components/base/BaseTableTh.vue',
    component: BaseTableTh,
    needs: UI,
    states: [
      {
        name: '普通表头',
        note: '不给 sortKey：就是一格 <th scope="col">，整格没有按钮、也进不了 Tab 顺序。',
        props: {},
        slot: '名字',
        expect: '名字',
      },
      {
        name: '右对齐的一列',
        note: 'align="end"：数值列贴右，数字的末位才对得齐。',
        props: { align: 'end' },
        slot: '用量',
        expect: '用量',
        expectSelector: 'th.btth--end',
      },
      {
        name: '有排序键，但没有表壳',
        note: '排序的开关住在表壳里（BaseTable 注入的 TABLE_SORT）：单独挂着时注入为空，键在、按钮不在，但这一列仍会写上 aria-sort="none"（还没排序）。',
        props: { sortKey: 'name' },
        slot: '名字',
        expectSelector: 'th[aria-sort="none"]',
      },
    ],
  },
  {
    id: 'tip-tap-editor',
    title: 'TipTapEditor',
    about:
      '题目、知识库、公告、团队简介共用的富文本编辑器：一排工具栏加正文区；v-model 的形状由 output 定（tiptap 的文档 JSON，或一段 HTML）。',
    file: 'src/components/common/Editor/TipTapEditor.vue',
    component: TipTapEditor,
    needs: UI_T,
    states: [
      {
        name: '空正文',
        note: '没给内容时是一篇空文档：正文区摆那句占位提示，工具栏上每一颗都画出来（做不成的置灰）。',
        props: {},
        expectSelector: '.ProseMirror',
      },
      {
        name: '一篇 JSON 正文',
        note: 'output="json"（默认）时 v-model 是 tiptap 的文档 JSON：打开就把这一段话画进正文区。',
        props: { modelValue: EDITOR_DOC },
        expect: '下周三的课改到线上，有课件的提前发我。',
      },
      {
        name: '一段 HTML（公告）',
        note: 'output="html" 时 v-model 是一段 HTML：公告和团队简介存的是这一种。',
        props: { output: 'html', modelValue: ANNOUNCEMENT_HTML },
        expect: '本周公告',
      },
    ],
  },
  {
    id: 'attachment-image-view',
    title: 'AttachmentImageView',
    about: '富文本里那张图：图只存一个附件 id，宽高按上传时量的原图尺寸先留位，下面还能挂一句图说。',
    file: 'src/components/common/Editor/AttachmentImageView.vue',
    component: AttachmentImageView,
    // 图注和「图片无法加载」那两句走的是全局的 t()，单独的 i18n 实例不用装。
    needs: [],
    states: [
      {
        name: '图的位置（还没读到）',
        note: '预览站没接附件来源（没有人 provide ATTACHMENT_IMAGE_SOURCE）：地址取不到，图框就照着原图的宽高比先把位置占住，正文不跳。',
        props: attachmentImageViewProps(),
        expectSelector: '.rt-image__frame',
      },
      {
        name: '被选中的一张',
        note: '在编辑器里点中它：图框外描一圈 --primary，不只换颜色。',
        props: attachmentImageViewProps({ selected: true }),
        expectSelector: '.rt-image.is-selected',
      },
      {
        name: '没有图说',
        note: '节点里没有图说、编辑器又可编辑：那一栏留着但空着（占位提示写在那儿），而不是整块消失。',
        props: attachmentImageViewProps({ caption: '' }),
        expectSelector: '.rt-image__caption.is-empty',
      },
    ],
  },
  {
    id: 'page-header',
    title: 'PageHeader',
    about: '内容区顶上的页头：一条标题（或一段面包屑）、页签、右侧操作区。面包屑、页签和操作区都由外壳注入。',
    file: 'src/components/common/PageHeader.vue',
    component: PageHeader,
    needs: UI,
    states: [
      {
        name: '一页的标题',
        note: '外壳没注入面包屑和操作区时，页头画的就是自己的标题 —— 这一格正是没人给它那些东西的样子。',
        props: { title: '项目设置' },
        expect: '项目设置',
      },
      {
        name: '抬头整段自己画',
        note: '给了默认插槽就不再画标题：整条抬头交给自己（一排面包屑、一段导航都行）。',
        props: {},
        slot: '成员 / 爱丽丝',
        expect: '成员 / 爱丽丝',
      },
      {
        name: '标题前带一个图标',
        note: 'icon 写在页头自己身上：没有面包屑可依时就画它。',
        props: { icon: 'mdi-cog-outline', title: '设置' },
        expect: '设置',
      },
    ],
  },
  {
    id: 'user-ref-link',
    title: 'UserRefLink',
    about: '句子里提到的一个人：显示名从外壳注入的名册来，点了去那个人的主页；没人注入时只画名字、去不了。',
    file: 'src/components/common/UserRefLink.vue',
    component: UserRefLink,
    needs: [],
    states: [
      {
        name: '认得的人',
        note: 'handle 和显示名都给：画成 @爱丽丝。预览站没注入名册，所以它点不动 —— 这一格看的是名字那一半。',
        props: { handle: 'alice', name: '爱丽丝' },
        expect: '@爱丽丝',
      },
      {
        name: '只知道名字',
        note: '没有 handle 就没有去处（找不到那个人的主页）：照样画 @名字，但这一颗不是可点的东西。',
        props: { name: '新来的同学' },
        expect: '@新来的同学',
      },
      {
        name: '没人注入名册时的兜底',
        note: '只给 handle、名册又没注入：查不到显示名就退回 handle，画成 @alice，照样没有去处。',
        props: { handle: 'alice' },
        expect: '@alice',
      },
    ],
  },
]
