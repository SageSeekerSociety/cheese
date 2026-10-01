<!--
  在手机上使用: the part of the download page that still means something inside
  the desktop app. The phone uses the web app, so this is the code to scan; it
  opens the download page on the phone, which walks through adding it to the
  Home Screen. Opened from the user menu, in the app only.
-->
<template>
  <AdaptiveDialog
    v-model="phoneOpen"
    :title="t('navigation.desktopApp.phoneTitle')"
    :cancel-label="t('navigation.desktopApp.close')"
    :max-width="360"
  >
    <div class="phone">
      <!-- eslint-disable-next-line vue/no-v-html -- an SVG this page draws from its own address -->
      <div class="phone__code" role="img" :aria-label="pageUrl" v-html="qr" />
      <p class="phone__hint">{{ t('navigation.desktopApp.phoneScan') }}</p>
    </div>
  </AdaptiveDialog>
</template>

<script setup lang="ts">
import { renderSVG } from 'uqr'

import { useDesktopApp } from '@/composables/useDesktopApp'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const { phoneOpen } = useDesktopApp()

const pageUrl = `${window.location.origin}/download`
const qr = renderSVG(pageUrl, { border: 0, blackColor: 'currentColor', whiteColor: 'transparent' })
</script>

<style scoped>
.phone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  text-align: center;
}

.phone__code {
  width: 160px;
  height: 160px;
  color: var(--ink);
}

.phone__code :deep(svg) {
  width: 100%;
  height: 100%;
}

.phone__hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
