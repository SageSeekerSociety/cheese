<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一). A confident dark
// mark: an inverse rounded-square + light glyph. NOT amber (amber is a rare
// accent, never an avatar). Shapes follow GitHub: people are circles, an AI
// teammate is a rounded square — the shape alone tells the two apart.
//
// 字取名字的第一个字，因为一个项目可以有好几个 AI 队友：写死的「芝」会让换过
// 队友的房间里，头像和它旁边的名字对不上。
import { computed } from 'vue'

import { avatarInitial, squareRadius } from '../utils/avatar'

const props = withDefaults(defineProps<{ size?: number | string; name?: string }>(), {
  size: 28,
  name: '芝士',
})

const glyph = computed(() => avatarInitial(props.name))
// 字号和圆角都跟着头像走，不跟着外面的字号走：同一个 20px 的头像放进 13px 的
// 事件行和放进 14px 的正文里，字不该一大一小；圆角按边长的比例取，缩小之后才不会
// 看着更圆，和旁边 28px 的那一个像同一个东西。
const px = computed(() => Number(props.size))
</script>

<template>
  <div
    class="cheese-avatar"
    :style="{
      width: px + 'px',
      height: px + 'px',
      fontSize: Math.round(px * 0.45) + 'px',
      borderRadius: squareRadius(px),
    }"
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
  /* 反色那一对：浅色下是近黑底白字；深色下不翻成白块，而是一块比页面亮一档的
     灰——白块在深色页面上是整列里最刺眼的东西。 */
  background: var(--inverse-surface);
  color: var(--inverse-ink);
  user-select: none;
}
.cheese-avatar__glyph {
  font-weight: 600;
  line-height: 1;
}
</style>
