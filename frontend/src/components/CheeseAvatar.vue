<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一)：一块超椭圆，里面
// 一双胶囊眼，一大一小，整体偏右，像把脸转向了说话的一方。样子和由来见
// docs/brand.md「AI 队友头像」。
//
// 形状：人是圆的，团队、空间、项目是圆角方块（squareRadius），AI 队友单独是超椭圆，
// 形状本身就把三者分开。底色不用琥珀（琥珀留给主操作）。
//
// 一个项目可以有好几个 AI 队友，它们的脸一样，靠底色分：底色按 handle 从一组深色里
// 取一档（agentTone），不按名字——队友改名之后颜色不能跟着变。没有 handle 的地方
// （官网示例、演示房间）拿名字当种子。
import { computed } from 'vue'

import { t } from '../i18n'
import { agentTone } from '../utils/avatar'

const props = withDefaults(defineProps<{ size?: number | string; name?: string; handle?: string | null }>(), {
  size: 28,
  name: undefined,
  handle: null,
})

// 没给名字时是默认的那一位：读屏读它的默认名，底色也按这个名字取。
const shownName = computed(() => props.name || t('work.agentAvatar.defaultName'))

const px = computed(() => Number(props.size))
const tone = computed(() => agentTone(props.handle || shownName.value))
</script>

<template>
  <svg
    class="cheese-avatar"
    :width="px"
    :height="px"
    viewBox="0 0 100 100"
    role="img"
    :aria-label="shownName"
    :data-tone="tone"
  >
    <path class="cheese-avatar__tile" d="M50 0C88 0 100 12 100 50S88 100 50 100S0 88 0 50S12 0 50 0Z" />
    <rect class="cheese-avatar__eye" x="42.5" y="44.5" width="13" height="21" rx="6.5" />
    <rect class="cheese-avatar__eye" x="66" y="46" width="10" height="16" rx="5" />
  </svg>
</template>

<style scoped>
.cheese-avatar {
  display: block;
  flex: 0 0 auto;
  user-select: none;
}
.cheese-avatar__eye {
  fill: var(--agent-ink);
}
.cheese-avatar[data-tone='0'] .cheese-avatar__tile {
  fill: var(--agent-tone-0);
}
.cheese-avatar[data-tone='1'] .cheese-avatar__tile {
  fill: var(--agent-tone-1);
}
.cheese-avatar[data-tone='2'] .cheese-avatar__tile {
  fill: var(--agent-tone-2);
}
.cheese-avatar[data-tone='3'] .cheese-avatar__tile {
  fill: var(--agent-tone-3);
}
.cheese-avatar[data-tone='4'] .cheese-avatar__tile {
  fill: var(--agent-tone-4);
}
</style>
