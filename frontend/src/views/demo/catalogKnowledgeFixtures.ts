/**
 * 知识库那几件（`components/teams/knowledge/*.vue`）在预览站里吃的数据。
 *
 * 形状**不是编的**：一条 `Knowledge` 就是 `types/knowledges.ts` 里那份声明
 * （`creator` 是 `types/users.ts` 的 `User`，`material` 是 `types/materials.ts`
 * 的 `Material`），少一个键多一个键都在 `vue-tsc` 那里当场红。值照抄页面那一层
 * 真实的传法：工具栏的 `resourceTypes` 就是 `lib/knowledgeFormat.ts` 的
 * `RESOURCE_TYPE_OPTIONS`（页也是把这张表原样递下去的），`availableTags` 是
 * `useTeamKnowledge` 从已取回的那几条里收出来的那种一串中文标签。
 *
 * 时间戳写死而不是 `Date.now()`：`formatDay` / `formatDetailDate` 要的是稳定的
 * 那几格字，跟着当天变的话这一份就不叫「真会出现的形状」了。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 七百多行、`catalog.ts` 已经八百多行，
 * 五条条目的数据塞进去会顶到 `frontend/src` 那一千行的上限；和 `catalogRail.ts`、
 * `catalogQueueFixtures.ts` 同一个理由。条目本身在 `catalogKnowledge.ts`，那边的
 * `CatalogEntry` 是 type-only 引用。
 */
import type { Knowledge, KnowledgeContentData, User } from '@/types'

import { RESOURCE_TYPE_OPTIONS } from '@/lib/knowledgeFormat'

/** 页面算「这一条归谁」时用的那个 id（`services/account.ts` 里当前登录的人）。 */
export const KNOWLEDGE_OWNER_ID = 1

/**
 * 两个作者：`我`（能删 —— 列表里那颗删除键按这个出现）和 `爱丽丝`（不能删）。
 * 两个都在，是为了让「按创建者给删除键」这条在预览站里看得见，而不是听人说。
 */
const ME: User = {
  id: KNOWLEDGE_OWNER_ID,
  username: 'cheese',
  nickname: '我',
  avatarId: 1,
  intro: '',
  question_count: 0,
  answer_count: 0,
}
const ALICE: User = {
  id: 2,
  username: 'alice',
  nickname: '爱丽丝',
  avatarId: 2,
  intro: '',
  question_count: 0,
  answer_count: 0,
}

/** 两个写死的时间：一条是「刚放上来」，一条是上一周的。 */
const RECENT = 1_700_000_000_000
const LAST_WEEK = 1_699_400_000_000

/** 一条资料。每一格只覆盖它真正在讲的那几个字段。 */
export function knowledgeRow(over: Partial<Knowledge> = {}): Knowledge {
  return {
    id: 1,
    name: '设计规范 v3',
    type: 'TEXT',
    content: JSON.stringify({ richText: { type: 'doc', content: [] } }),
    teamId: 7,
    labels: [],
    creator: ME,
    sourceChannel: { id: 3, name: '设计频道' },
    createdAt: RECENT,
    updatedAt: RECENT,
    ...over,
  }
}

/** 网格和列表看的是同一份：四条，四种类型，两个作者，一条带缩略图。 */
export const KNOWLEDGE_ROWS: Knowledge[] = [
  knowledgeRow({
    id: 1,
    name: '设计规范 v3',
    description: '颜色、间距、圆角那一套。',
    type: 'MATERIAL',
    material: {
      id: 11,
      type: 'image',
      url: 'https://cdn.example.com/materials/design-system.png',
      meta: {
        width: 1600,
        height: 900,
        size: 248_320,
        thumbnail: 'https://cdn.example.com/materials/design-system-thumb.png',
      },
    },
    thumbnail: 'https://cdn.example.com/materials/design-system-thumb.png',
    labels: ['设计', '前端', '规范', '多余的一个'],
  }),
  knowledgeRow({
    id: 2,
    name: '周会纪要',
    description: '这次周会定下的三件事，以及各自归谁。',
    creator: ALICE,
    labels: ['会议'],
    createdAt: LAST_WEEK,
    updatedAt: LAST_WEEK,
    sourceChannel: { id: 5, name: '产品频道' },
  }),
  knowledgeRow({
    id: 3,
    name: '官网',
    description: '对外那一页。',
    type: 'LINK',
    url: 'https://cheese.example.com',
    content: JSON.stringify({ url: 'https://cheese.example.com', title: 'Cheese 官网', description: '对外那一页。' }),
  }),
  knowledgeRow({
    id: 4,
    name: '分页查询',
    description: '列表接口那一套 pageStart / pageSize。',
    type: 'CODE',
    creator: ALICE,
    content: JSON.stringify({
      code: 'const page = await list({ pageStart: 0, pageSize: 20 })',
      language: 'typescript',
    }),
    labels: ['后端'],
    createdAt: LAST_WEEK,
    updatedAt: LAST_WEEK,
  }),
]

/** 详情对话框要挂的几条：一条一种预览（图 / 文档 / 链接 / 代码）。 */
export const DETAIL_IMAGE = KNOWLEDGE_ROWS[0]
export const DETAIL_FILE = knowledgeRow({
  id: 5,
  name: '需求说明',
  type: 'MATERIAL',
  material: {
    id: 15,
    type: 'file',
    url: 'https://cdn.example.com/materials/spec.docx',
    meta: {
      name: '需求说明.docx',
      size: 51_200,
      mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      expires: 1_800_000_000,
    },
  },
  labels: ['需求'],
})
export const DETAIL_LINK = KNOWLEDGE_ROWS[2]
export const DETAIL_CODE = KNOWLEDGE_ROWS[3]

/** 详情对话框里那三份内容（页是拿 `parseKnowledgeContent` 从 `content` 解出来的）。 */
export const LINK_CONTENT: KnowledgeContentData = {
  url: 'https://cheese.example.com',
  title: 'Cheese 官网',
  description: '对外那一页。',
}

export const CODE_CONTENT: KnowledgeContentData = {
  code: 'const page = await list({ pageStart: 0, pageSize: 20 })',
  language: 'typescript',
}

/** 原始讨论那一块要的那条消息：`sender` 是 `User`，`createdAt` 是毫秒。 */
export const DETAIL_ORIGINAL_MESSAGE = {
  content: '这条放知识库吧，新来的先看它。',
  sender: ALICE,
  createdAt: RECENT,
}

/** 工具栏那六样。`resourceTypes` 就是页递下去的那张表。 */
export function knowledgeToolbarProps(
  over: {
    searchQuery?: string | null
    typeFilter?: string | null
    tagFilter?: string | null
    viewMode?: string
    availableTags?: string[]
  } = {}
) {
  return {
    searchQuery: null as string | null,
    typeFilter: null as string | null,
    tagFilter: null as string | null,
    viewMode: 'grid',
    resourceTypes: RESOURCE_TYPE_OPTIONS,
    availableTags: KNOWLEDGE_TAGS,
    ...over,
  }
}

/** 标签下拉里的选项：从上面那四条里收出来的那几个（页也是这么收的）。 */
export const KNOWLEDGE_TAGS = ['设计', '前端', '规范', '多余的一个', '会议', '后端']

/** 详情对话框那四样。 */
export function knowledgeDetailProps(
  over: {
    modelValue?: boolean
    resource?: Knowledge | null
    content?: KnowledgeContentData
    ownerId?: number
  } = {}
) {
  return {
    modelValue: true,
    resource: DETAIL_IMAGE as Knowledge | null,
    content: {} as KnowledgeContentData,
    ownerId: KNOWLEDGE_OWNER_ID,
    ...over,
  }
}

/** 上传对话框那三样。 */
export function knowledgeUploadProps(over: { modelValue?: boolean; uploading?: boolean } = {}) {
  return {
    modelValue: true,
    uploading: false,
    availableTags: KNOWLEDGE_TAGS,
    ...over,
  }
}
