<template>
  <!-- Every project tile goes through here (docs/design-system.md §3.14); the square,
       the image fallback and the theme-invariant ink are UserAvatar's. -->
  <UserAvatar
    kind="org"
    :avatar="image"
    :name="name"
    :seed="id"
    :label="label"
    :color="color"
    :size="size"
    :alt="alt"
    :style="{ fontSize: `${fontPx}px` }"
  >
    <v-icon v-if="icon" :icon="`mdi-${icon}`" :size="iconPx" class="project-avatar__icon" />
    <template v-else>{{ label }}</template>
  </UserAvatar>
</template>

<script setup lang="ts">
// 项目格子：挑过的图标、表情、文字或图片，没挑过就是从项目名取的字（projectInitials）。
// 底色挑过用挑的色相，没挑过按项目 id 派生——改名不换色。规则在 utils/projectAvatar.ts。
import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'
import { projectAvatarColor, type ProjectAvatarSpec, projectInitials } from '@/utils/projectAvatar'

import UserAvatar from '@/components/common/UserAvatar.vue'

const {
  id,
  name = '',
  avatar = null,
  size = 32,
  alt = '',
} = defineProps<{
  /** 项目 id：没挑颜色时底色的种子。 */
  id: string
  name?: string
  /** 后端下发的 `avatar`；null 或缺省是默认格子。 */
  avatar?: ProjectAvatarSpec | null
  size?: number | string
  /** 这一格是唯一身份时读屏念的名字；留空是装饰（旁边有项目名）。 */
  alt?: string
}>()

const px = computed(() => {
  const n = Number.parseFloat(String(size))
  return Number.isFinite(n) ? n : 32
})

const kind = computed(() => avatar?.kind ?? null)
const image = computed(() => (kind.value === 'image' && avatar?.image_id ? getAvatarUrl(avatar.image_id) : ''))
const icon = computed(() => (kind.value === 'icon' ? avatar?.glyph ?? '' : ''))
const label = computed(() => {
  if ((kind.value === 'emoji' || kind.value === 'text') && avatar?.glyph) return avatar.glyph
  // 图片取不到时 UserAvatar 退回这一格的字：仍是项目名取的字。
  return projectInitials(name)
})
const color = computed(() => projectAvatarColor(id, avatar?.hue))

// 字越多字号越小，四个字母也放得下；表情略大一点，单个字形才看得清。
const fontPx = computed(() => {
  if (kind.value === 'emoji') return Math.round(px.value * 0.56)
  const chars = [...label.value]
  const wide = chars.some((ch) => /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(ch))
  const ratio = chars.length <= 1 ? 0.5 : chars.length === 2 ? (wide ? 0.36 : 0.42) : chars.length === 3 ? 0.32 : 0.27
  return Math.max(8, Math.round(px.value * ratio))
})
const iconPx = computed(() => Math.round(px.value * 0.6))
</script>

<style scoped>
/* 底色两套主题同值（avatarColor 的固定亮度），图标跟字一样用 --avatar-ink，不能随主题变。 */
.project-avatar__icon {
  color: var(--avatar-ink);
}
</style>
