<script setup lang="ts">
// 反馈里「谁写的」那一格的头像。反馈中心、详情页、评论、管理端四处共用一个 ——
// 同一个判断在七个地方各写一遍，是同一个人的头像在列表里是彩色的、在评论里变成
// 深色方块那种 bug 的来源。
//
// 两个分支的依据是服务端给的 `author_is_agent`，**不是** handle 长什么样：
// 「谁写的」和「谁按的发送」是两件事（提案卡就是 agent 写、由人发的），只有这个
// 字段说得清，而按 handle 猜会在换过命名规则的那天悄悄猜错。
//
// 真人走 `UserAvatar`（handle 派生的彩色首字母）：反馈接口只回 handle，**没有头像
// URL**，所以拿不到人自己传的那张图。这不是这里偷懒 —— `utils/avatar.ts` 就是全站
// 「这个人没设头像」时的兜底，反馈这一屏和别处长得一样，而不是自己发明一张灰脸。
// 要显示真头像得让后端在反馈接口上把 handle 解析成用户档案（平台级查询，不是这一屏
// 自己的事），记在话题文档的待办里。
//
// agent 走 `CheeseAvatar`（深色方块 + 名字首字），和聊天面板、现场面板同一个标记。
//
// 头像在这里不是装饰：一屏长列表里最先被眼睛抓住的就是「这条是谁提的」，而一行纯
// 文字 handle 在滚动时几乎不可区分。
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

const {
  handle,
  isAgent = false,
  size = 24,
} = defineProps<{
  /** 作者（或评论者、时间线上那个人）的 handle。 */
  handle: string
  /** 服务端的 `author_is_agent` / `by_is_agent`。 */
  isAgent?: boolean
  size?: number | string
}>()
</script>

<template>
  <CheeseAvatar v-if="isAgent" class="fb-avatar" :size="size" :name="handle" />
  <UserAvatar v-else class="fb-avatar" :size="size" :name="handle" />
</template>

<style scoped>
/* 和旁边的文字对齐：flex 行里的头像默认按基线对齐会往下掉，`middle` 让它待在
   handle 那一行的中间。 */
.fb-avatar {
  vertical-align: middle;
  flex: 0 0 auto;
}
</style>
