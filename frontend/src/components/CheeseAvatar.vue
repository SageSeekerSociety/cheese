<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一). A confident dark
// mark: an inverse rounded-square + light glyph. NOT amber (amber is a rare
// accent, never an avatar). Rounded-square to match the human avatars.
//
// 字取名字的第一个字，因为一个项目可以有好几个 AI 队友：写死的「芝」会让换过
// 队友的房间里，头像和它旁边的名字对不上。
import { computed } from 'vue'

import { avatarInitial } from '../utils/avatar'

const props = withDefaults(defineProps<{ size?: number | string; name?: string }>(), {
  size: 28,
  name: '芝士',
})

const glyph = computed(() => avatarInitial(props.name))
// 字号跟着头像走，不跟着外面的字号走：同一个 20px 的头像放进 13px 的事件行和
// 放进 14px 的正文里，字不该一大一小。小号头像的圆角降一档，否则缩小之后看着
// 更圆，和旁边 28px 的那一个不像同一个东西。
const px = computed(() => Number(props.size))
</script>

<template>
  <div
    class="cheese-avatar"
    :class="{ 'cheese-avatar--small': px <= 20 }"
    :style="{ width: px + 'px', height: px + 'px', fontSize: Math.round(px * 0.45) + 'px' }"
  >
    <span class="cheese-avatar__glyph">{{ glyph }}</span>
  </div>
</template>

<style scoped>
.cheese-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border-radius: var(--radius-md);
  /* 反色那一对：浅色下是近黑底白字；深色下不翻成白块，而是一块比页面亮一档的
     灰——白块在深色页面上是整列里最刺眼的东西。 */
  background: var(--inverse-surface);
  color: var(--inverse-ink);
  user-select: none;
}
.cheese-avatar--small {
  border-radius: var(--radius-sm);
}
.cheese-avatar__glyph {
  font-weight: 600;
  line-height: 1;
}
</style>
