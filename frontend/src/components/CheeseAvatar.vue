<script setup lang="ts">
// AI 队友的头像 — one identity everywhere (spec §8.4 人格唯一). A confident dark
// mark: solid --ink rounded-square + white glyph. NOT amber (amber is a rare
// accent, never an avatar). Rounded-square 8px to match the human avatars.
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
</script>

<template>
  <div class="cheese-avatar" :style="{ width: size + 'px', height: size + 'px' }">
    <span class="cheese-avatar__glyph">{{ glyph }}</span>
  </div>
</template>

<style scoped>
.cheese-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border-radius: 8px;
  background: var(--ink);
  /* --ink inverts with the theme (near-black → near-white), so the glyph on it
     must invert too: --surface is #fff in light (unchanged), #1B1D20 in dark. */
  color: var(--surface);
  user-select: none;
}
.cheese-avatar__glyph {
  font-weight: 600;
  line-height: 1;
  font-size: 0.52em;
}
</style>
