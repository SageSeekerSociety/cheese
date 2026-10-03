<!--
  重启以完成更新: in the desktop app's top bar while a new version of the app
  has downloaded and waits for a restart. Nothing restarts until it is clicked;
  closing the window instead lets the app install it out of sight
  (desktop/src-tauri/src/updates.rs). An older app never reports this, so there
  it never shows.
-->
<template>
  <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
  <v-btn
    v-if="status?.state === 'ready'"
    class="update-ready"
    variant="text"
    color="primary"
    :loading="restarting"
    :title="t('navigation.desktopApp.ready', { version: status.version })"
    @click="restart"
  >
    <v-icon size="14" aria-hidden="true">mdi-arrow-up-circle-outline</v-icon>
    <span class="update-ready__label">{{ t('navigation.desktopApp.restart') }}</span>
  </v-btn>
</template>

<script setup lang="ts">
import { useDesktopApp } from '@/composables/useDesktopApp'

import { t } from '@/i18n'

const { status, restarting, restart } = useDesktopApp()
</script>

<style scoped>
/* The same shape as the rest of this cluster (HelpAndFeedbackMenu): 28 high, no border. */
.update-ready {
  height: 28px;
  padding: 0 8px;
  font-size: 13px;
}

.update-ready__label {
  margin-left: 4px;
}
</style>
