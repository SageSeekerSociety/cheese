<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BrandLockup from '@/components/common/BrandLockup.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'
import { docsUrl } from '@/lib/docsSite'
import { ANONYMOUS_VISITOR, PUBLIC_VISITOR } from '@/lib/publicVisitor'

// The bar and footer shared by the public pages: the homepage for the people who
// build projects, the solutions page for the schools, companies and research
// teams that bring them in, and the download page.

defineProps<{ page: 'home' | 'solutions' | 'download' }>()

// 登录状态从外壳注入（lib/publicVisitor.ts），不是自己读 services/account：这只外壳
// 一旦连着后端，拿它包着用的三个公共页也跟着连上，目录站里就挂不起来。没人注入时按
// 「没登录」画——公共页本来就是给没登录的人看的。
const { signedIn } = inject(PUBLIC_VISITOR, ANONYMOUS_VISITOR)

// okcheese.cn is served from the mainland, so its home page shows the ICP
// filing number, linked to the MIIT register.
const ICP_FILING = '京ICP备2026068086号' // i18n-data: a registry identifier, written the same in every language

// `/` sends a signed-in visitor to their work, so the way back to the
// introduction for them is `/about`, which always shows it.
const homeHref = computed(() => (signedIn.value ? '/about' : '/'))
const entryHref = computed(() => (signedIn.value ? '/' : '/account/signin'))
const entryLabel = computed(() => (signedIn.value ? t('publicSite.openWorkspace') : t('publicSite.getStarted')))

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
           separate site (docsUrl), so that one is a real navigation. The same
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
        <a :href="docsUrl()">{{ t('publicSite.navDocs') }}</a>
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
        <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener" lang="zh-CN">{{ ICP_FILING }}</a>
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

/* The ink on the amber solid buttons, on every door page.
   Vuetify derives it from `primary` and it comes out white — 2.65:1 on #f57f17,
   below even the 3:1 glyph floor, so the arrow beside the label fails too. The
   amber is the brand and does not move; the label does. Setting the variable the
   button already reads (`bg-primary` puts
   `color: rgb(var(--v-theme-on-primary)) !important` on it) colours the label
   and its icons in one declaration, without fighting that utility.

   On the button rather than on `.landing`: the button carries
   `v-theme--light` / `v-theme--dark` itself, and that class re-declares this
   variable per theme (white in light, Vuetify's own black in dark), so a value
   inherited from above loses to it on the element that reads it. Declaring it on
   the element itself is what wins.

   Unscoped, like the rules above, and not in landing.css: the amber buttons are
   not all rendered by the component whose copy of that file is scoped to them —
   Download.vue's own and the hero's product mock are somebody else's template —
   and `:deep()` is not available in a standalone .css file. `.landing` as the
   ancestor keeps this on the door pages.

   One pinned value, identical in both themes, the way the rail's brand mark pins
   its own (components/common/Navigation/RailItem.vue): the amber is bright under
   either theme, so the ink on it has to stay dark under both. `var(--ink)` cannot
   be used — in dark theme it is #f3f4f6, 1.76:1 on #ffa733, worse than the white
   it replaces. #191a1c is 6.58:1 on the light amber and 8.98:1 on the dark one.

   The app's own solid buttons are the same shape and keep Vuetify's white:
   moving them changes the whole product's look and is a separate decision
   (docs/design-system.md §2.6 对比度). */
.landing .v-btn.bg-primary {
  --v-theme-on-primary: 25, 26, 28;
}
</style>
