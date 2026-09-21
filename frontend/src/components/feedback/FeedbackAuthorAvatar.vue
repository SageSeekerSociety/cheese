<script setup lang="ts">
// 反馈里「谁写的」那一格的头像。反馈中心、详情页、评论、管理端四处共用一个 ——
// 同一个判断在七个地方各写一遍，是同一个人的头像在列表里是彩色的、在评论里变成
// 深色方块那种 bug 的来源。
//
// 两个分支的依据是服务端给的 `author_is_agent`，**不是** handle 长什么样：
// 「谁写的」和「谁按的发送」是两件事（提案卡就是 agent 写、由人发的），只有这个
// 字段说得清，而按 handle 猜会在换过命名规则的那天悄悄猜错。
//
// 真人的头像是后端解析出来的：接口回的是 `author_avatar_id`，即作者**自己挑过**的
// 那张图的素材 id。平台惯例是只回 id、URL 由前端拼（`utils/materials.getAvatarUrl`），
// 和名册、成员列表同一套。这里只做「有 id 就拼 URL，没有就交回彩色首字母」，
// **不做**「这是不是那张全局默认头像」的判断 —— 那个判断在服务端
// (`UserProfileRepository.chosen_avatar_ids`)：注册时人人都被写上默认头像，
// 所以「有 avatar_id」不等于「挑过」，而哪一行是默认图是各环境的种子数据。
//
// 所以 `avatarId` 为 null 时**必须**走首字母：`getAvatarUrl` 对空值返回的是
// `/avatars/default`，也就是「所有没挑过头像的人共用同一张脸」，那比按 handle 派生
// 的彩色首字母更难把人区分开 —— 而区分人正是头像唯一的活。传参前先判空。
//
// agent 走 `CheeseAvatar`（深色方块 + 名字首字），和聊天面板、现场面板同一个标记；
// 它不看 `avatarId`：agent 没有「自己挑的图」这回事。
//
// 头像在这里不是装饰：一屏长列表里最先被眼睛抓住的就是「这条是谁提的」，而一行纯
// 文字 handle 在滚动时几乎不可区分。
import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

const {
  handle,
  isAgent = false,
  avatarId = null,
  size = 24,
} = defineProps<{
  /** 作者（或评论者、备注者）的 handle。 */
  handle: string
  /** 服务端的 `author_is_agent`。 */
  isAgent?: boolean
  /** 服务端的 `author_avatar_id`：作者自己挑过的头像，没挑过是 null。 */
  avatarId?: number | null
  size?: number | string
}>()

// 空串 = 没有图，`UserAvatar` 的 `hasAvatar` 判的就是空串，会去画彩色首字母。
const avatarUrl = computed(() => (avatarId == null ? '' : getAvatarUrl(avatarId)))
</script>

<template>
  <CheeseAvatar v-if="isAgent" class="fb-avatar" :size="size" :name="handle" />
  <UserAvatar v-else class="fb-avatar" :size="size" :name="handle" :avatar="avatarUrl" />
</template>

<style scoped>
/* 和旁边的文字对齐：flex 行里的头像默认按基线对齐会往下掉，`middle` 让它待在
   handle 那一行的中间。 */
.fb-avatar {
  vertical-align: middle;
  flex: 0 0 auto;
}
</style>
