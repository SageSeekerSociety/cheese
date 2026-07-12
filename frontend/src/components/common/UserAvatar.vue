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
  color: #fff;
  font-weight: 600;
}
</style>
