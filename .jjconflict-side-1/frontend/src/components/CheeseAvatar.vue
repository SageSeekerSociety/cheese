<script setup lang="ts">
// 芝士's avatar — one identity everywhere (spec §8.4 人格唯一). A confident dark
// mark: solid --ink rounded-square + white 芝 glyph. NOT amber (amber is a rare
// accent, never an avatar). Rounded-square 8px to match the human avatars.
withDefaults(defineProps<{ size?: number | string }>(), { size: 28 })
</script>

<template>
  <div class="cheese-avatar" :style="{ width: size + 'px', height: size + 'px' }">
    <span class="cheese-avatar__glyph">芝</span>
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
