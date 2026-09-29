<script setup lang="ts">
import { computed } from 'vue'

import logo from '@/assets/logo-plain.svg?url'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'
import AccountService from '@/services/account'

// The bar and footer shared by the public pages: the homepage for the people who
// build projects, and the solutions page for the schools, companies and research
// teams that bring them in.

defineProps<{ page: 'home' | 'solutions' }>()

const loggedIn = computed(() => AccountService.loggedIn)
// `/` sends a signed-in visitor to their work, so the way back to the
// introduction for them is `/about`, which always shows it.
const homeHref = computed(() => (loggedIn.value ? '/about' : '/'))
const entryHref = computed(() => (loggedIn.value ? '/' : '/account/signin'))
const entryLabel = computed(() => (loggedIn.value ? t('publicSite.openWorkspace') : t('publicSite.getStarted')))
</script>

<template>
  <main id="top" class="landing" :lang="i18n.global.locale.value">
    <header class="site-bar">
      <a class="brand" :href="homeHref" :aria-label="t('publicSite.cheeseHome')">
        <span class="brand-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="brand-word">cheese</span>
        <span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
      </a>
      <!-- Plain links, not router-links: the router keeps the scroll position across
           navigation, so arriving from the bottom of one page would land mid-way
           down the other. -->
      <nav class="site-nav" :aria-label="t('publicSite.mainNavigation')">
        <a :href="page === 'home' ? '#story' : homeHref" :aria-current="page === 'home' ? 'page' : undefined">
          {{ t('publicSite.navProduct') }}
        </a>
        <a href="/solutions" :aria-current="page === 'solutions' ? 'page' : undefined">
          {{ t('publicSite.navSolutions') }}
        </a>
        <a :href="page === 'home' ? '#download' : `${homeHref}#download`">{{ t('publicSite.navDownload') }}</a>
      </nav>
      <div class="site-actions">
        <LanguageToggle />
        <v-btn :href="entryHref" variant="outlined" append-icon="mdi-arrow-top-right">{{ entryLabel }}</v-btn>
      </div>
    </header>

    <slot :entry-href="entryHref" :entry-label="entryLabel" />

    <footer class="site-foot">
      <span class="brand">
        <span class="brand-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="brand-word">cheese</span>
        <span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('global.cheese') }}</span>
      </span>
      <span>{{ t('publicSite.slogan') }}</span>
      <nav class="site-foot-links" :aria-label="t('publicSite.legal')">
        <router-link to="/legal/terms">{{ t('publicSite.terms') }}</router-link>
        <router-link to="/legal/privacy">{{ t('publicSite.privacy') }}</router-link>
        <span>{{ t('publicSite.copyright') }}</span>
      </nav>
    </footer>
  </main>
</template>

<style scoped src="./landing.css"></style>
<style>
/* The workspace locks document scrolling; the public pages scroll as documents. */
html:has(.landing) {
  overflow-y: auto !important;
}

body:has(.landing),
#app:has(.landing) {
  height: auto;
  overflow: visible;
}
</style>
