/**
 * 预览站「共用件」和两个整页（`catalogShared.ts` / `catalogViews.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着各组件自己的 `.spec.ts` 造：富文本那两份文档抄
 * `TipTapEditor.spec.ts` 的样本，产物版本抄 `ArtifactVersionList.room.spec.ts` 的
 * `version()`，小队简介抄 `TeamProfile.spec.ts` 的 `team()`。附件图那一件是 tiptap 的
 * 节点视图，它收的是编辑器塞给它的那一组 props，这里按 `nodeViewProps` 的形状拼一份
 * （图只存附件 id，宽高按上传时量的原图尺寸）。
 *
 * 这里只出数据（外加两个把参数拼成 props 的小函数），不引任何运行时依赖：哪一份配
 * 哪个组件、每一格看什么，在 `catalogShared.ts` / `catalogViews.ts`。
 */
import type { JSONContent } from '@tiptap/core'
import type { ArtifactVersion } from '@/api'
import type { Team, User } from '@/types'

// ---- 富文本（TipTapEditor）---------------------------------------------------

/** 一篇存成 JSON 的正文：题目、知识库、模板里存的就是这一种。 */
export const EDITOR_DOC: JSONContent = {
  type: 'doc',
  content: [{ type: 'paragraph', content: [{ type: 'text', text: '下周三的课改到线上，有课件的提前发我。' }] }],
}

/** 一段存成 HTML 的正文：公告和团队简介存的是这一种。 */
export const ANNOUNCEMENT_HTML =
  '<h2 style="text-align: center">本周公告</h2><p>下周三的课改到线上，有课件的提前发我。</p>'

// ---- 附件图（AttachmentImageView，tiptap 的节点视图）------------------------

/**
 * 拼一份节点视图收到的 props（`nodeViewProps` 那一组）。
 *
 * `editor.isEditable` 决定图说那一栏画不画；`node.content.size` 为零就是没有图说；
 * 宽高是上传时量的原图尺寸，图到之前先按这个比例留位。
 */
export function attachmentImageViewProps(
  over: { selected?: boolean; attachmentId?: number | null; alt?: string; caption?: string } = {}
): Record<string, unknown> {
  const { selected = false, attachmentId = 42, alt = '堆的布局', caption = '图 1：堆的布局' } = over
  return {
    editor: { isEditable: true },
    node: {
      type: { name: 'attachmentImage' },
      attrs: { attachmentId, alt, title: null, width: 1280, height: 720 },
      content: { size: caption.length },
    },
    decorations: [],
    selected,
    extension: { name: 'attachmentImage', options: {} },
    getPos: () => 0,
    updateAttributes: () => {},
    deleteNode: () => {},
    view: {},
    innerDecorations: {},
    HTMLAttributes: {},
  }
}

// ---- 产物版本（ArtifactVersionList）-----------------------------------------

/** 一版：没写的字段照接口给的「空」补齐。 */
function artifactVersion(
  over: Partial<ArtifactVersion> & Pick<ArtifactVersion, 'number' | 'card_id'>
): ArtifactVersion {
  return {
    subject: null,
    delivered_at: null,
    decided_by: null,
    kind: 'file',
    filename: null,
    url: null,
    bytes: null,
    room: null,
    ...over,
  }
}

/** 一项产物交付过的三版：一份文件、一个网址、一次合并。最新的在最上面。 */
export const ARTIFACT_VERSIONS: ArtifactVersion[] = [
  artifactVersion({
    number: 1,
    card_id: 'card-1',
    subject: 'feat: 把月度报告导成 PDF',
    kind: 'file',
    filename: 'report-2026-09.pdf',
    bytes: 241_000,
    delivered_at: '2026-09-28T08:00:00Z',
    decided_by: 'linxia',
    room: { id: 'r1', title: '定价' },
  }),
  artifactVersion({
    number: 2,
    card_id: 'card-2',
    subject: 'fix: 表头的数值右对齐',
    kind: 'link',
    url: 'https://example.com/reports/2',
    delivered_at: '2026-09-30T10:00:00Z',
    decided_by: 'cheese',
    room: { id: 'r2', title: '作业提交' },
  }),
  artifactVersion({
    number: 3,
    card_id: 'card-3',
    subject: 'chore: 换成新的模板',
    kind: 'merge',
    delivered_at: '2026-10-01T10:00:00Z',
  }),
]

/** 交出去的不是一份文件的那两版：一次合并、交付物留存之前递的卡。 */
export const ARTIFACT_VERSIONS_WITHOUT_FILE: ArtifactVersion[] = [
  artifactVersion({ number: 1, card_id: 'card-1', subject: 'docs: 先写一页说明', kind: null }),
  artifactVersion({ number: 2, card_id: 'card-2', subject: 'feat: 落地这一版', kind: 'merge' }),
]

/** 做了很多版：先摆最近四版，剩下的收进一颗「显示更早的 N 版」。 */
export const ARTIFACT_VERSIONS_MANY: ArtifactVersion[] = Array.from({ length: 6 }, (_, i) =>
  artifactVersion({
    number: i + 1,
    card_id: `card-${i + 1}`,
    subject: `feat: 第 ${i + 1} 版的改动`,
    kind: 'file',
    filename: `v${i + 1}.pdf`,
    bytes: 120_000 + i * 1_000,
    delivered_at: `2026-09-${String(20 + i).padStart(2, '0')}T08:00:00Z`,
  })
)

// ---- 小队简介（TeamProfile）--------------------------------------------------

/** 一个人：`owner` 要一整条 `User`，把必填字段都补上。 */
function user(over: Partial<User> & Pick<User, 'id' | 'username' | 'nickname'>): User {
  return {
    avatarId: 0,
    intro: '',
    question_count: 0,
    answer_count: 0,
    ...over,
  } as User
}

/** 一份小队简介：`/teams/:handle` 那页看到的形状。 */
export function teamProfile(over: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'zhishi',
    name: '知是',
    intro: '一起把课件和作业放上平台，每周碰一次。',
    avatarId: 1,
    owner: user({ id: 1, username: 'cheese', nickname: '芝士' }),
    admins: { total: 1, examples: [user({ id: 2, username: 'linxia', nickname: '林夏' })] },
    members: { total: 12, examples: [] },
    joinStatus: 'none',
    joinApproval: true,
    ...over,
  } as Team
}
