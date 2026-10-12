<script setup lang="ts">
// 凡是要画「这是谁」的地方都用它：头像 + 姓名（+ 可选一行 @handle）。人和队友的分支、
// 缺头像的降级只在这一处 —— 别各页面各抄一份（抄出来的那份迟早漏了 agent 判断）。
// 纯 props，不 fetch：头像素材 id、显示名、是不是队友都由调用方给（接口回 id，URL 由
// utils/materials 拼）。查不到这个人时给 avatarId: null，它画按 handle 派生的彩色首字母。
//
// 判「是不是队友」先认 `agent`（名册在手就照它填），没给则按 handle 命名规则兜底
// （cheese / cheese-*）——**只认得出队友，认不出的人一律当人**，绝不按名字长相乱猜。
//
// `agent` 是三态、不是 `boolean`：Vue 会把「没传的布尔 prop」钉成 `false`（不是
// undefined），那样兜底就永远轮不到 —— 名册缺席的 AI 会被画成真人。所以这里显式默认
// `null`（未知），只有调用方真的说了 true/false 才作数。
import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import CheeseAvatar from '../CheeseAvatar.vue'

import UserAvatar from './UserAvatar.vue'

import { isAgentHandle } from '@/lib/authorship'

const props = withDefaults(
  defineProps<{
    /** 认人的唯一依据：头像底色的种子、`handleLine` 行的内容。 */
    handle?: string | null
    /** 显示名。不给就退回 handle。 */
    name?: string | null
    /** 自己挑过的头像素材 id；null = 没挑过，画彩色首字母。 */
    avatarId?: number | null
    /** 是不是 AI 队友。名册在手就照名册填 true/false；不填（null）则按 handle 命名规则兜底。 */
    agent?: boolean | null
    size?: number | string
    /** 名字前加 `@`：句子里提到一个人时的那颗 chip。 */
    mention?: boolean
    /** 名字下另起一行画 `@handle`。 */
    handleLine?: boolean
    /** 头像对读屏的名字；留空即装饰（旁边本就有可读的名字）。 */
    alt?: string
  }>(),
  {
    handle: null,
    name: null,
    avatarId: null,
    agent: null,
    size: 24,
    mention: false,
    handleLine: false,
    alt: '',
  }
)

const label = computed(() => props.name || props.handle || '')
const avatarUrl = computed(() => (props.avatarId == null ? '' : getAvatarUrl(props.avatarId)))
const isAgent = computed(() => props.agent ?? isAgentHandle(props.handle || ''))
</script>

<template>
  <span class="identity-view">
    <CheeseAvatar v-if="isAgent" class="identity-view__face" :size="size" :name="label" :handle="handle || undefined" />
    <UserAvatar
      v-else
      class="identity-view__face"
      :size="size"
      :name="label"
      :seed="handle || label"
      :avatar="avatarUrl"
      :alt="alt"
    />
    <span class="identity-view__text">
      <span class="identity-view__name"><template v-if="mention">@</template>{{ label }}</span>
      <span v-if="handleLine && handle" class="identity-view__handle">@{{ handle }}</span>
    </span>
  </span>
</template>

<style scoped>
.identity-view {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.identity-view__face {
  flex: 0 0 auto;
}
.identity-view__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: var(--lh-14);
}
.identity-view__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.identity-view__handle {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
