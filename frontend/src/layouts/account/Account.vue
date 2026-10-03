<template>
  <div class="account-shell" :class="{ 'account-shell--title-bar': titleBarInset }">
    <!-- The brand scene belongs to the layout, not to a page, so moving between
         sign-in, sign-up and recovery never draws it again. Where the desktop
         app draws its title bar over the page, this pane and the bar above the
         form are what move the window. -->
    <aside class="account-art" :data-tauri-drag-region="titleBarInset ? 'deep' : undefined">
      <BrandScene :narrow="narrow" />
      <router-link to="/" class="account-brand account-art__brand" :aria-label="t('account.layout.home')">
        <BrandLockup />
      </router-link>
      <p class="account-art__fine">{{ t('global.copyright', { year }) }}</p>
    </aside>

    <div class="account-side">
      <header class="account-bar" :data-tauri-drag-region="titleBarInset ? 'deep' : undefined">
        <LanguageToggle />
      </header>

      <main class="account-main">
        <div class="account-column">
          <v-defaults-provider :defaults="defaults">
            <router-view v-slot="{ Component }">
              <transition name="account-page" mode="out-in">
                <component :is="Component" />
              </transition>
            </router-view>
          </v-defaults-provider>
        </div>
      </main>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import BrandLockup from '@/components/common/BrandLockup.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import { t } from '@/i18n'
import { titleBarOverlay } from '@/lib/desktopApp'

const year = new Date().getFullYear()
const titleBarInset = titleBarOverlay()
const { mdAndUp } = useDisplay()

// Below the md breakpoint the scene is a short band above the form.
const narrow = computed(() => !mdAndUp.value)

// The label sits above each field (AccountField), so no field needs the
// details row for clearance and it can go when there is nothing to say.
const defaults = {
  VTextField: {
    variant: 'outlined',
    density: 'compact',
    hideDetails: 'auto',
  },
  VOtpInput: {
    variant: 'outlined',
  },
  VBtn: {
    elevation: 0,
  },
  VAlert: {
    border: false,
  },
}
</script>

<style scoped>
.account-shell {
  position: relative;
  display: grid;
  grid-template-columns: 5fr 7fr;
  min-height: 100dvh;
  background: var(--surface);
}

/* ---- The brand pane ---- */

.account-art {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  height: 100dvh;
  padding: 24px 32px;
  overflow: hidden;
  background: var(--canvas);
}

/* The macOS window buttons sit in the top-left corner; the mark goes below them. */
.account-shell--title-bar .account-art {
  padding-top: 48px;
}

.account-art__brand,
.account-art__fine {
  position: relative;
  z-index: var(--z-raised);
}

.account-art__fine {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  pointer-events: none;
}

.account-brand {
  --brand-h: 24px;

  display: flex;
  align-self: flex-start;
  color: var(--ink);
  text-decoration: none;
}

/* ---- The form side ---- */

.account-side {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.account-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 16px 24px;
}

/* Every page's heading starts at the same height, so moving between pages
   of different length changes what is below it, never where it is. Centring
   would move the heading with every field a page adds. */
.account-main {
  display: flex;
  flex: 1;
  align-items: flex-start;
  justify-content: center;
  padding: clamp(48px, 18vh, 168px) 24px 64px;
}

.account-column {
  width: 100%;
  max-width: 360px;
}

/* ---- Fields: label above (AccountField), a quiet box, an amber ring on focus ---- */

.account-column :deep(.v-field--variant-outlined) {
  --v-field-input-min-height: 44px;

  border-radius: var(--radius-md);
  box-shadow: 0 0 0 0 transparent;
  transition: box-shadow var(--dur-quick) var(--ease-standard);
}

.account-column :deep(.v-field--variant-outlined .v-field__outline) {
  --v-field-border-opacity: 1;

  color: var(--line-2);
}

.account-column :deep(.v-field--variant-outlined.v-field--focused) {
  box-shadow: 0 0 0 3px var(--accent-wash);
}

.account-column :deep(.v-field--variant-outlined.v-field--focused .v-field__outline) {
  --v-field-border-width: 1px;

  color: var(--focus-ring);
}

.account-column :deep(.v-field--variant-outlined.v-field--error .v-field__outline) {
  color: var(--danger);
}

/* Hints and errors line up with the label above the box, not inside it. */
.account-column :deep(.v-input__details) {
  padding-inline: 0;
}

/* ---- The pieces every account page is made of ---- */

/* A link reads as ink with a faint underline; amber is kept for the one
   primary button on the page (design-system §9.9). */
.account-column :deep(.account-link) {
  padding: 0;
  font: inherit;
  font-weight: 500;
  color: var(--ink);
  text-decoration: underline;
  text-decoration-color: var(--line-2);
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
  cursor: pointer;
  background: none;
  border: 0;
  transition: text-decoration-color var(--dur-quick) var(--ease-standard);
}

.account-column :deep(.account-link:hover) {
  text-decoration-color: currentcolor;
}

.account-column :deep(.account-link:disabled) {
  color: var(--faint);
  cursor: default;
}

.account-column :deep(.account-link--quiet) {
  font-weight: 400;
  color: var(--muted);
  text-decoration-color: transparent;
}

.account-column :deep(.v-btn--size-large) {
  height: 44px;
}

.account-column :deep(.account-submit) {
  margin-top: 8px;
  font-size: 15px;
}

.account-column :deep(.account-secondary),
.account-column :deep(.v-btn--variant-outlined) {
  border-color: var(--line-2);
}

.account-column :deep(.account-actions) {
  display: grid;
  gap: 10px;
}

.account-column :deep(.account-foot) {
  margin-top: 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.account-column :deep(.account-foot--split) {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 16px;
}

.account-column :deep(.account-foot__wait) {
  color: var(--faint);
}

.account-column :deep(.account-fine) {
  margin-top: 24px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.account-column :deep(.account-hint) {
  margin: 4px 0 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
}

.account-column :deep(.account-otp) {
  padding: 0;
  margin-bottom: 8px;
}

/* ---- Moving between pages: the old one leaves quickly, the new one settles in ---- */

.account-page-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.account-page-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.account-page-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.account-page-leave-to {
  opacity: 0;
}

/* ---- Phone and narrow tablet: the scene is a band above the form ---- */

@media (max-width: 959.98px) {
  /* The band keeps its height; any room left over goes to the form. */
  .account-shell {
    grid-template-rows: auto 1fr;
    grid-template-columns: 1fr;
  }

  .account-art {
    position: relative;
    height: 240px;
    padding: 16px 20px;
  }

  .account-art__fine {
    display: none;
  }

  .account-bar {
    position: absolute;
    top: 0;
    right: 0;
    z-index: var(--z-raised-2);
    padding: 12px 16px;
  }

  .account-main {
    padding: 28px 24px 48px;
  }

  .account-column {
    max-width: 400px;
  }
}
</style>
