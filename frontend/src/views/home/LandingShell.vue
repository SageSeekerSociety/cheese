<script setup lang="ts">
import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BrandLockup from '@/components/common/BrandLockup.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'
import AccountService from '@/services/account'

// The bar and footer shared by the public pages: the homepage for the people who
// build projects, the solutions page for the schools, companies and research
// teams that bring them in, and the download page.

defineProps<{ page: 'home' | 'solutions' | 'download' }>()

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
      <router-link class="brand" :to="homeHref" :aria-label="t('publicSite.cheeseHome')">
        <BrandLockup />
      </router-link>
      <!-- Router links, so moving between the public pages does not reload the app;
           the router's scrollBehavior lands each one at its top. The docs are a
           separate site under /docs/, so that one is a real navigation. -->
      <nav class="site-nav" :aria-label="t('publicSite.mainNavigation')">
        <router-link
          :to="page === 'home' ? { hash: '#story' } : homeHref"
          :aria-current="page === 'home' ? 'page' : undefined"
        >
          {{ t('publicSite.navProduct') }}
        </router-link>
        <router-link to="/solutions" :aria-current="page === 'solutions' ? 'page' : undefined">
          {{ t('publicSite.navSolutions') }}
        </router-link>
        <router-link to="/download" :aria-current="page === 'download' ? 'page' : undefined">
          {{ t('publicSite.navDownload') }}
        </router-link>
        <a href="/docs/">{{ t('publicSite.navDocs') }}</a>
      </nav>
      <div class="site-actions">
        <LanguageToggle />
        <BaseButton :to="entryHref" kind="secondary" append-icon="mdi-arrow-top-right">{{ entryLabel }}</BaseButton>
      </div>
    </header>

    <slot :entry-href="entryHref" :entry-label="entryLabel" />

    <footer class="site-foot">
      <span class="brand">
        <BrandLockup />
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
