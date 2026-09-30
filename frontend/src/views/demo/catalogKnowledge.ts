/**
 * 知识库那几件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，六条条目和它们的几格
 * 状态塞进去会顶到 `frontend/src` 那一千行的上限 —— 和 `catalogRail.ts`、
 * `catalogDashboard.ts`、`catalogQueue.ts`、`catalogChat.ts` 同一个理由（那几件
 * 也都是从一个大页里拆出来的）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `KNOWLEDGE_ENTRIES`
 * 这个值，运行时不构成循环。
 *
 * 为什么这六件值得一站：它们以前是 1508 行的 `views/teams/detail/Knowledge.vue`
 * 里的四块模板加两个对话框，想看其中任何一块都得先起假后端、把整页拉起来、再等
 * 那一页的请求回来；拆开以后每一件都只吃 props，于是每一件都能单独摆在预览站里看。
 * 数据见 `catalogKnowledgeFixtures.ts`。
 *
 * 六件都是 `frontend_grade.py` 的 A 级（只吃 props、只往上发事件）：取数、写入、
 * 「这一条归谁」全在 `composables/useTeamKnowledge.ts` 和 `lib/knowledgeFormat.ts`
 * 里，所以这个目录里没有一条要装假后端。
 *
 * 观感也各归各家，不靠页那一层的样式：详情对话框是 `v-dialog`，内容被传送到
 * `body`，页写得再对也够不着它（`views/teams/detail/Knowledge.spec.ts` 的
 * 「样式归属」钉着这件事），所以 `.code-block` 的规则长在 `KnowledgeDetailDialog.vue`
 * 里，`.resource-preview` 的四支分类色长在 `KnowledgeGrid.vue` 里，颜色字面量在
 * `src/style.css` 的 `--category-*` / `--code-bg` / `--code-ink`。也就是说这几格
 * 在预览站里跟产品是同一套规则，不是「长得像」。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  CODE_CONTENT,
  DETAIL_CODE,
  DETAIL_FILE,
  DETAIL_IMAGE,
  DETAIL_LINK,
  DETAIL_ORIGINAL_MESSAGE,
  KNOWLEDGE_ROWS,
  knowledgeDetailProps,
  knowledgeRow,
  knowledgeToolbarProps,
  knowledgeUploadProps,
  LINK_CONTENT,
} from './catalogKnowledgeFixtures'

import KnowledgeDetailDialog from '@/components/teams/knowledge/KnowledgeDetailDialog.vue'
import KnowledgeEmpty from '@/components/teams/knowledge/KnowledgeEmpty.vue'
import KnowledgeGrid from '@/components/teams/knowledge/KnowledgeGrid.vue'
import KnowledgeTable from '@/components/teams/knowledge/KnowledgeTable.vue'
import KnowledgeToolbar from '@/components/teams/knowledge/KnowledgeToolbar.vue'
import KnowledgeUploadDialog from '@/components/teams/knowledge/KnowledgeUploadDialog.vue'

/** 六件都吃 vuetify（`v-text-field` / `v-select` / `v-card` / `v-dialog` 那一套）。 */
const UI: CatalogNeed[] = ['vuetify']

export const KNOWLEDGE_ENTRIES: CatalogEntry[] = [
  {
    id: 'knowledge-toolbar',
    title: 'KnowledgeToolbar',
    about: '知识库页顶上那一行：搜索、资料类型、标签、上传、网格 / 列表切换。',
    file: 'src/components/teams/knowledge/KnowledgeToolbar.vue',
    component: KnowledgeToolbar,
    needs: UI,
    states: [
      {
        name: '什么都没筛',
        note: '三个筛选各自把新值报上去（`update:*`），再补一个 `change` 说「刚才那一下是筛选」—— 所以这一件里一行取数都没有，重不重取一页是页的事。',
        props: knowledgeToolbarProps(),
        expect: '搜索知识库',
      },
      {
        name: '三个筛选都挂着',
        note: '类型那一栏给的是中文名（`RESOURCE_TYPE_OPTIONS`），请求里要的 type code 由页翻（`resourceTypeCode`）—— 所以这里和下拉的文案是同一张表。',
        props: knowledgeToolbarProps({ searchQuery: '设计', typeFilter: '文件', tagFilter: '前端' }),
        expect: '上传资料',
      },
      {
        name: '停在列表视图',
        note: '右上那颗切换器只报「人点了哪一档」：两个视图读的是同一份已经取回来的列表，换视图不打接口。',
        props: knowledgeToolbarProps({ viewMode: 'list' }),
        expect: '上传资料',
      },
    ],
  },
  {
    id: 'knowledge-empty',
    title: 'KnowledgeEmpty',
    about: '一条资料都没有时那一块：两句话分岔，取决于「有没有在筛」。',
    file: 'src/components/teams/knowledge/KnowledgeEmpty.vue',
    component: KnowledgeEmpty,
    needs: UI,
    states: [
      {
        name: '一条都没有',
        note: '没筛过时说「先去聊天里放点东西进来」：这一档没有可点的东西，所以不给按钮。',
        props: { hasFilters: false },
        expect: '在频道聊天中添加有价值的内容到知识库',
      },
      {
        name: '筛空了',
        note: '和「一条都没有」不是一回事：筛过的时候说「换个条件试试」，不然人会以为这一页坏了。',
        props: { hasFilters: true },
        expect: '没有找到匹配当前筛选条件的资料',
      },
    ],
  },
  {
    id: 'knowledge-grid',
    title: 'KnowledgeGrid',
    about: '网格视图：一条一张卡，卡上是类型、名字、描述、标签、添加者。',
    file: 'src/components/teams/knowledge/KnowledgeGrid.vue',
    component: KnowledgeGrid,
    needs: UI,
    states: [
      {
        name: '四种类型各一条',
        note: '类型名、图标、几月几号都由 `lib/knowledgeFormat.ts` 算 —— 同一张表网格、表格、详情三处共用，所以同一个类型名不会在三种视图里各写一遍。卡顶那块预览的底色是四支分类色（规则在这一件里，值见 `src/style.css` 的 `--category-*`），所以这一格的底色就是产品里的底色。',
        props: { items: KNOWLEDGE_ROWS },
        expect: '设计规范 v3',
      },
      {
        name: '标签超过三个',
        note: '卡上只画前三个，剩下那条数收成 `+n`（列表视图里标签是折两行的，两处不一样是刻意的）。',
        props: { items: [KNOWLEDGE_ROWS[0]] },
        expect: '+1',
      },
      {
        name: '一条都没有',
        note: '空列表就是一片空 —— 「什么都没有」那一块是另一件（`KnowledgeEmpty`），页按「取回来是不是空的」二选一。',
        props: { items: [] },
      },
    ],
  },
  {
    id: 'knowledge-table',
    title: 'KnowledgeTable',
    about: '列表视图：一行一条，比网格多一列「操作」（看 / 打开 / 删）。',
    file: 'src/components/teams/knowledge/KnowledgeTable.vue',
    component: KnowledgeTable,
    needs: UI,
    states: [
      {
        name: '看着自己的和别人的',
        note: '删除键按 `canEditKnowledge` 给 —— 也就是「是不是自己放上去的」（`ownerId` 由页递进来，件够不着账号服务）。我自己那两条有，爱丽丝那两条没有。',
        props: { items: KNOWLEDGE_ROWS, ownerId: 1 },
        expect: '周会纪要',
      },
      {
        name: '没有「我」这个人',
        note: '`ownerId` 不给时一个删除键都不画：判不出来「归谁」就不给删，而不是先给了再说。',
        props: { items: KNOWLEDGE_ROWS },
        expect: '添加时间',
      },
      {
        name: '一条都没有',
        note: '空列表画出来的是一张只有表头的空表。',
        props: { items: [], ownerId: 1 },
        expect: '资料名称',
      },
    ],
  },
  {
    id: 'knowledge-detail-dialog',
    title: 'KnowledgeDetailDialog',
    about: '资料详情：预览（图 / 视频 / 音频 / 文档 / 富文本 / 链接 / 代码）+ 资料信息 + 原始讨论。',
    file: 'src/components/teams/knowledge/KnowledgeDetailDialog.vue',
    component: KnowledgeDetailDialog,
    needs: UI,
    teleport: true,
    states: [
      {
        name: '图片资料（我放的）',
        note: '能删那一行是页算好的：`ownerId` 和创建者对得上才有「删除资料」，而删之前的确认框和接口都在页那边。',
        props: knowledgeDetailProps({ resource: DETAIL_IMAGE }),
        expect: '删除资料',
      },
      {
        name: '文档资料（带原始讨论）',
        note: '文档这一档画的是文件名 + 大小 + 一颗「打开文档」；底下多一块原始讨论，那一句话和它的时间是记录里带上来的。',
        props: knowledgeDetailProps({
          resource: { ...DETAIL_FILE, originalMessage: DETAIL_ORIGINAL_MESSAGE },
        }),
        expect: '打开文档',
      },
      {
        name: '链接资料',
        note: '链接读的是 `content` 那一格（`KnowledgeContentData`），标题留空时退回资料名称 —— 换算在页那边，这一件拿到的已经是一份算好的内容。',
        props: knowledgeDetailProps({ resource: DETAIL_LINK, content: LINK_CONTENT }),
        expect: '访问链接',
      },
      {
        name: '代码片段',
        note: '底色和字色是刻意反色的那一对，规则就在这一件里，值在 `src/style.css` 的 `--code-bg` / `--code-ink`（浅色深底浅字；深色换成比 surface 亮一档的 `--fill-2` + `--text`，不然会糊进卡片）。',
        props: knowledgeDetailProps({ resource: DETAIL_CODE, content: CODE_CONTENT }),
        expect: '代码片段',
      },
      {
        name: '没有关联的原始讨论',
        note: '没有 `originalMessage` 时不画空壳，说一句「没有关联的原始讨论信息」。',
        props: knowledgeDetailProps({ resource: knowledgeRow({ id: 9, name: '零散笔记' }) }),
        expect: '没有关联的原始讨论信息',
      },
    ],
  },
  {
    id: 'knowledge-upload-dialog',
    title: 'KnowledgeUploadDialog',
    about: '「添加资料」对话框：选类型、填这一档要的那几格、提交。',
    file: 'src/components/teams/knowledge/KnowledgeUploadDialog.vue',
    component: KnowledgeUploadDialog,
    needs: UI,
    teleport: true,
    states: [
      {
        name: '打开时停在「文件」那一档',
        note: '草稿和校验都是它自己的（填到一半的东西只有它知道），每打开一次就是新的一张表；校验过了才把整份草稿报上去（`submit`）。',
        props: knowledgeUploadProps(),
        expect: '添加资料',
      },
      {
        name: '上传中',
        note: '`uploading` 是 props 进来的而不是它自己管的：传材料、建记录、说一句话、决定关不关门全在外面，它只负责把按钮转起来、按住不让再点。',
        props: knowledgeUploadProps({ uploading: true }),
        expect: '添加资料',
      },
    ],
  },
]
