<template>
  <div class="account-shell" :class="{ 'account-shell--plain': plain }">
    <!-- The brand scene belongs to the layout, not to a page, so moving between
         sign-in, sign-up and recovery never draws it again. -->
    <aside v-if="!plain" class="account-art">
      <BrandScene :narrow="narrow" />
      <router-link to="/" class="account-brand account-art__brand" :aria-label="t('account.layout.home')">
        <span class="account-brand__mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="account-brand__word">cheese</span>
        <span v-if="locale === 'zh-CN'" class="account-brand__cn">{{ t('global.cheese') }}</span>
      </router-link>
      <p class="account-art__fine">{{ t('global.copyright', { year }) }}</p>
    </aside>

    <div class="account-side">
      <header class="account-bar">
        <router-link v-if="plain" to="/" class="account-brand" :aria-label="t('account.layout.home')">
          <span class="account-brand__mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
          <span class="account-brand__word">cheese</span>
          <span v-if="locale === 'zh-CN'" class="account-brand__cn">{{ t('global.cheese') }}</span>
        </router-link>
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

      <footer v-if="plain" class="account-footer">{{ t('global.copyright', { year }) }}</footer>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'

import logo from '@/assets/logo-plain.svg?url'
import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'

const locale = i18n.global.locale
const year = new Date().getFullYear()
const route = useRoute()
const { mdAndUp } = useDisplay()

// A page that interrupts someone already signed in (re-verifying before a
// sensitive change) is not a way in, so it gets no brand scene: a quiet page
// they finish and leave (design-system §0.2, principles 4 and 8).
const plain = computed(() => !!route.meta.plain)
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

.account-shell--plain {
  grid-template-columns: 1fr;
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

.account-art__brand,
.account-art__fine {
  position: relative;
  z-index: 1;
}

.account-art__fine {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  pointer-events: none;
}

.account-brand {
  display: flex;
  align-items: center;
  align-self: flex-start;
  gap: 8px;
  color: var(--ink);
  text-decoration: none;
}

.account-brand__mark {
  display: block;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
  background: var(--ink);
}

.account-brand__word {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 800;
  line-height: var(--lh-18);
  letter-spacing: -0.04em;
}

.account-brand__cn {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
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

.account-shell--plain .account-bar {
  justify-content: space-between;
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

.account-footer {
  padding: 16px 24px 24px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
  text-align: center;
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
  .account-shell {
    grid-template-columns: 1fr;
  }

  /* The band keeps its height; any room left over goes to the form. */
  .account-shell:not(.account-shell--plain) {
    grid-template-rows: auto 1fr;
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
    z-index: 2;
    padding: 12px 16px;
  }

  .account-shell--plain .account-bar {
    position: static;
  }

  .account-main {
    padding: 28px 24px 48px;
  }

  .account-column {
    max-width: 400px;
  }
}
</style>
