import type { RouteRecordRaw } from 'vue-router'

/**
 * 课程链接的落点：管理员把 `/spaces/join/<code>` 发进群里，成员点开落到这一页。
 *
 * 它只决定第一屏落在哪，不决定成员能看到什么 —— 加入之后他随时能回到完整
 * 平台，也能建自己的个人项目。所以这一页没有任何「课程专用界面」的假设，
 * 它做完「加入 + 有自己的项目」就把人交给课程第一屏。
 *
 * 单独一个路由文件、并且注册在 `spaces` 之前：`/spaces/:spaceId` 那一棵是按
 * 段匹配的，这里多一段，但顺序上先注册掉更不容易被将来的通配吃掉。
 */
export default {
  path: '/spaces/join/:code',
  name: 'SpacesJoinCourse',
  component: () => import('@/views/spaces/JoinCourse.vue'),
  meta: {
    title: '加入课程',
    titleKey: 'spaces.joinCourse.title',
    isFullPage: true,
  },
} as RouteRecordRaw
