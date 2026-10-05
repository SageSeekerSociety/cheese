<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

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

// On a phone the nav links do not fit beside the lockup, so the same links
// live behind a disclosure button instead: without it /download and /docs/ are
// unreachable from the homepage. `Esc` closes it and hands focus back to the
// button that opened it.
// Opening moves focus to the first link, since the panel sits before the button
// in the DOM and Tab would otherwise walk past it. A press outside the header
// closes it, and so does widening past the breakpoint, so a stale open state
// never comes back when the window narrows again.
const menuOpen = ref(false)
const menuToggle = ref<HTMLButtonElement | null>(null)
const siteBar = ref<HTMLElement | null>(null)

function closeMenu() {
  if (!menuOpen.value) return
  menuOpen.value = false
  menuToggle.value?.focus()
}

async function toggleMenu() {
  menuOpen.value = !menuOpen.value
  if (!menuOpen.value) return
  await nextTick()
  siteBar.value?.querySelector<HTMLElement>('#site-menu a')?.focus()
}

function onPointerDown(e: PointerEvent) {
  if (menuOpen.value && !siteBar.value?.contains(e.target as Node)) menuOpen.value = false
}

const narrow = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(width <= 900px)') : null
function onBreakpoint(e: MediaQueryListEvent) {
  if (!e.matches) menuOpen.value = false
}
onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  narrow?.addEventListener('change', onBreakpoint)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  narrow?.removeEventListener('change', onBreakpoint)
})
</script>

<template>
  <main id="top" class="landing" :class="{ 'menu-open': menuOpen }" :lang="i18n.global.locale.value">
    <header ref="siteBar" class="site-bar" @keydown.esc="closeMenu">
      <router-link class="brand" :to="homeHref" :aria-label="t('publicSite.cheeseHome')">
        <BrandLockup />
      </router-link>
      <!-- Router links, so moving between the public pages does not reload the app;
           the router's scrollBehavior lands each one at its top. The docs are a
           separate site under /docs/, so that one is a real navigation. The same
           element is the phone disclosure panel; a click closes it so the panel
           does not stay over the page the router just opened. -->
      <nav id="site-menu" class="site-nav" :aria-label="t('publicSite.mainNavigation')" @click="menuOpen = false">
        <router-link
          :to="page === 'home' ? { hash: '#story' } : homeHref"
          :aria-current="page === 'home' ? 'page' : undefined"
        >
          {{ t('publicSite.navProduct') }}
        </router-link>
        <router-link :to="page === 'home' ? { hash: '#use-cases' } : { path: homeHref, hash: '#use-cases' }">
          {{ t('publicSite.navUseCases') }}
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
        <button
          ref="menuToggle"
          type="button"
          class="site-menu-toggle"
          :aria-expanded="menuOpen"
          aria-controls="site-menu"
          @click="toggleMenu"
        >
          <span class="visually-hidden">{{ t('publicSite.menu') }}</span>
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
            <path
              d="M3 5.5h14M3 10h14M3 14.5h14"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            />
          </svg>
        </button>
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
