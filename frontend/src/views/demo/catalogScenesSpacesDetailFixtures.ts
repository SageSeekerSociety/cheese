/**
 * 预览站「题目板拆出来的那半张画面」第二批（`catalogScenesSpacesDetail.ts` 的条目）读的数据。
 *
 * 全是产品里真会出现的形状，照着各自的 `.spec.ts` 和所在的 `XView.vue` 造：成员行照
 * `MembersView.vue` 自己导出的 `Row`。这里只出数据，不引运行时要装的东西：哪一份配哪个
 * 组件、每一格看什么，在 `catalogScenesSpacesDetail.ts`。
 */
import type { SpaceAnnouncement } from '@/types/spaces'
import type { Row } from '@/views/spaces/detail/MembersView.vue'

// ---- 成员（MembersView）----------------------------------------------------

/** 成员表里的一行：`MembersView.vue` 导出的 `Row`。`viaCode` 为空就是「没记录」。 */
export function memberRow(over: Partial<Row> = {}): Row {
  return {
    userId: 11,
    handle: 'ada',
    name: '阿达',
    avatarId: 101,
    role: 'MEMBER',
    viaCode: 'K7PM3QXW',
    ...over,
  }
}

// ---- 公告（AnnouncementsView）----------------------------------------------

/** 一条空间公告：`types/spaces.ts` 的 `SpaceAnnouncement`。`expiresAt` 非空就是有到期日。 */
export function announcement(over: Partial<SpaceAnnouncement> = {}): SpaceAnnouncement {
  return {
    id: 1,
    spaceId: 8,
    title: '学期安排',
    content: '<p>下周一开课。</p>',
    pinned: false,
    expiresAt: null,
    createdAt: 1757000000000,
    updatedAt: 1757000000000,
    author: { id: 1, username: 'cheese', nickname: '芝士', avatarId: null },
    ...over,
  }
}
