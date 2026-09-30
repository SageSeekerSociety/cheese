<script setup lang="ts">
import { computed } from 'vue'

import WordmarkEn from '@/assets/brand/wordmark-en.svg?component'
import WordmarkZh from '@/assets/brand/wordmark-zh.svg?component'
import logo from '@/assets/logo-plain.svg?url'
import i18n from '@/i18n'

// The in-product lockup: the single-colour mark and one wordmark, both in the
// surrounding text colour. Chinese pages show 知是, all others cheese; each
// wordmark file carries its own name as aria-label, so what is read out is
// what is drawn. The caller sets the mark's height with --brand-h; the
// proportions below are the ones docs/brand.md fixes for each language.
const zh = computed(() => i18n.global.locale.value === 'zh-CN')
// Quoted: the mark is small enough to be inlined as a data: URL, which is not
// a valid unquoted url() token, and an invalid mask-image drops the whole mask.
const markStyle = { maskImage: `url("${logo}")` }
</script>

<template>
  <span class="brand-lockup">
    <span class="brand-lockup__mark" :style="markStyle" aria-hidden="true" />
    <component
      :is="zh ? WordmarkZh : WordmarkEn"
      class="brand-lockup__word"
      :class="zh ? 'brand-lockup__word--zh' : 'brand-lockup__word--en'"
      role="img"
    />
  </span>
</template>

<style scoped>
.brand-lockup {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: calc(var(--brand-h, 26px) * 0.3);
  color: inherit;
}

.brand-lockup__mark {
  display: block;
  flex-shrink: 0;
  width: var(--brand-h, 26px);
  height: var(--brand-h, 26px);
  background: currentcolor;
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
}

.brand-lockup__word {
  display: block;
  flex-shrink: 0;
  width: auto;
}

.brand-lockup__word--zh {
  height: calc(var(--brand-h, 26px) * 0.54);
}

.brand-lockup__word--en {
  height: calc(var(--brand-h, 26px) * 0.5);
}
</style>
