<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一). A confident dark
// mark: an inverse rounded-square + light glyph. NOT amber (amber is a rare
// accent, never an avatar). Shapes follow GitHub: people are circles, an AI
// teammate is a rounded square — the shape alone tells the two apart.
//
// 字取名字的第一个字，因为一个项目可以有好几个 AI 队友：写死的「芝」会让换过
// 队友的房间里，头像和它旁边的名字对不上。
//
// 底色也按名字算（和人的头像同一套 avatarColor）。名字怎么起都行，常有好几位
// 第一个字相同（芝士GLM、芝士派），一排同色的「芝」只能靠读名字分辨；换成各自
// 的底色，一眼就分得开。种子用名字而不是 handle：用的地方大多只拿得到名字，
// 而且改了名字、字变了，底色跟着变也说得通。
import { computed } from 'vue'

import { t } from '../i18n'
import { avatarColor, avatarInitial, squareRadius } from '../utils/avatar'

const props = withDefaults(defineProps<{ size?: number | string; name?: string }>(), {
  size: 28,
  name: undefined,
})

// 没给名字时是默认的那一位：字和底色都按它的默认名取，不然会是一块中性灰。
const shownName = computed(() => props.name || t('work.agentAvatar.defaultName'))
const glyph = computed(() => avatarInitial(shownName.value))
const ground = computed(() => avatarColor(shownName.value))
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
      backgroundColor: ground,
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
  /* 底色是 avatarColor() 按名字给的（内联），两套主题一样；它保证白字过 4.5:1，
     所以字是写死的白，和人的头像同一个理由（room-row.css 的 .im-avatar）。 */
  /* stylelint-disable-next-line color-no-hex -- 压在头像底色上的墨色，底色不随主题变。 */
  color: #fff;
  user-select: none;
}
.cheese-avatar__glyph {
  font-weight: 600;
  line-height: 1;
}
</style>
