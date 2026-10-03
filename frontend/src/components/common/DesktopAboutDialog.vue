<!--
  「关于知是」in the desktop app: which app and which web build this window
  runs, and where the app's own update stands. Opened from the help menu, and on
  macOS from the app menu's "About Cheese" and "Check for Updates…". The
  marketing site stays in the browser: its links here open there.
-->
<template>
  <AdaptiveDialog
    v-model="aboutOpen"
    :title="t('navigation.desktopApp.about')"
    :cancel-label="t('navigation.desktopApp.close')"
    :max-width="400"
  >
    <div class="about">
      <img :src="appIcon" alt="" width="64" height="64" class="about__icon" />
      <dl class="about__versions">
        <dt>{{ t('navigation.desktopApp.appVersion') }}</dt>
        <dd class="t-num">{{ appVersion ?? t('navigation.desktopApp.earlierVersion') }}</dd>
        <dt>{{ t('navigation.desktopApp.webVersion') }}</dt>
        <dd class="t-num">{{ webBuild ?? t('navigation.desktopApp.devBuild') }}</dd>
      </dl>

      <div class="about__update" role="status" aria-live="polite">
        <template v-if="!canCheck">
          <p class="about__line">{{ t('navigation.desktopApp.updatesItself') }}</p>
        </template>
        <template v-else-if="status?.state === 'ready'">
          <p class="about__line about__line--strong">
            {{ t('navigation.desktopApp.ready', { version: status.version }) }}
          </p>
          <BaseButton kind="primary" :loading="restarting" @click="restart">{{
            t('navigation.desktopApp.restart')
          }}</BaseButton>
          <p class="about__line">{{ t('navigation.desktopApp.readyHint') }}</p>
        </template>
        <template v-else>
          <p v-if="line" class="about__line">{{ line }}</p>
          <BaseButton kind="secondary" :loading="busy" :disabled="busy" @click="check">{{
            t('navigation.desktopApp.check')
          }}</BaseButton>
        </template>
      </div>

      <div v-if="linksOut" class="about__links">
        <a href="/download#changelog" @click.prevent="openInBrowser('/download#changelog')">{{
          t('navigation.desktopApp.changelog')
        }}</a>
        <a href="/about" @click.prevent="openInBrowser('/about')">{{ t('navigation.desktopApp.website') }}</a>
      </div>
    </div>
  </AdaptiveDialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useDesktopApp } from '@/composables/useDesktopApp'

import appIcon from '@/assets/app-icon.png'
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { desktopAppVersion, desktopCan, openInBrowser } from '@/lib/desktopApp'

const { status, aboutOpen, canCheck, restarting, check, restart } = useDesktopApp()

const appVersion = desktopAppVersion()
// The commit this web build was made from (frontend/Dockerfile's GIT_SHA); a local build has none.
const webBuild = import.meta.env.VITE_WEB_BUILD?.slice(0, 7) || null
const linksOut = desktopCan('links')

const busy = computed(() => status.value?.state === 'checking' || status.value?.state === 'downloading')

const line = computed(() => {
  const now = status.value
  switch (now?.state) {
    case 'checking':
      return t('navigation.desktopApp.checking')
    case 'downloading':
      return t('navigation.desktopApp.downloading', { version: now.version })
    case 'latest':
      return t('navigation.desktopApp.latest')
    case 'failed':
      return t('navigation.desktopApp.failed')
    default:
      return ''
  }
})
</script>

<style scoped>
.about {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
  text-align: center;
}

.about__icon {
  width: 64px;
  height: 64px;
}

.about__versions {
  display: grid;
  grid-template-columns: auto auto;
  gap: 4px 16px;
  font-size: 14px;
  line-height: var(--lh-14);
}

.about__versions dt {
  color: var(--muted);
  text-align: end;
}

.about__versions dd {
  color: var(--text);
  text-align: start;
}

.about__update {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.about__line {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.about__line--strong {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.about__links {
  display: flex;
  gap: 16px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.about__links a {
  color: var(--muted);
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
