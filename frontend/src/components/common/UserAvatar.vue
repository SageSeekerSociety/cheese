<template>
  <v-avatar :size="size" :style="hasAvatar ? undefined : { backgroundColor: fallbackColor }">
    <v-img v-if="hasAvatar" :src="avatar">
      <!-- Real avatar failed to load → colored initial, not a broken tile. -->
      <template #error>
        <span class="user-avatar-char" :style="{ backgroundColor: fallbackColor }">{{ initial }}</span>
      </template>
    </v-img>
    <span v-else class="user-avatar-char">{{ initial }}</span>
  </v-avatar>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { avatarColor, avatarInitial } from '@/utils/avatar'

const {
  avatar = '',
  name = '',
  size = 48,
} = defineProps<{
  /** URL of an uploaded avatar image. Empty → colored-initial fallback. */
  avatar?: string
  /** Display name / handle used for the initial + deterministic color. */
  name?: string
  size?: string | number
}>()

const hasAvatar = computed(() => !!avatar)
const initial = computed(() => avatarInitial(name))
const fallbackColor = computed(() => avatarColor(name))
</script>

<style scoped>
.user-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  line-height: 1;
  /* 这个 #fff 是故意保留的写死值，别"顺手修好"它：底色是 avatarColor() 算出来的
     hsl(色相, 55%, 55%)，两套主题下同一个值，所以压在上面的字也不该随主题变。
     换成 var(--ink)/on-surface 会在深色下变成浅灰压浅底，反而更糟。
     （另注：这个底色本身在黄绿色相段上对白字只有约 1.7:1，两个主题都读不清——
      那是 avatarColor() 的问题，不属于深色适配，另有一张卡处理。） */
  color: #fff;
  font-weight: 600;
}
</style>
