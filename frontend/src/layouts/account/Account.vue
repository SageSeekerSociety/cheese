<template>
  <div class="account-shell">
    <header class="account-bar">
      <router-link to="/" class="account-brand" :aria-label="t('account.layout.home')">
        <span class="account-brand__mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span class="account-brand__word">cheese</span>
        <span v-if="locale === 'zh-CN'" class="account-brand__cn">{{ t('global.cheese') }}</span>
      </router-link>
      <LanguageToggle />
    </header>

    <div class="account-main">
      <div class="account-column">
        <v-defaults-provider :defaults="defaults">
          <router-view v-slot="{ Component }">
            <v-fade-transition mode="out-in">
              <component :is="Component" />
            </v-fade-transition>
          </router-view>
        </v-defaults-provider>
      </div>
    </div>

    <footer class="account-footer">{{ t('global.copyright', { year }) }}</footer>
  </div>
</template>

<script lang="ts" setup>
import logo from '@/assets/logo-plain.svg?url'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import i18n, { t } from '@/i18n'

const locale = i18n.global.locale
const year = new Date().getFullYear()

const defaults = {
  VTextField: {
    variant: 'outlined',
    density: 'comfortable',
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
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  background: var(--surface);
}

.account-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
}

.account-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  text-decoration: none;
}

.account-brand__mark {
  display: block;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
  background: var(--ink);
}

.account-brand__word {
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 800;
  line-height: var(--lh-23);
  letter-spacing: -0.04em;
}

.account-brand__cn {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.account-main {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  padding: 32px 24px 48px;
}

.account-column {
  width: 100%;
  max-width: 400px;
}

.account-footer {
  padding: 16px 24px 24px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
  text-align: center;
}

@media (max-width: 599.98px) {
  .account-bar {
    padding: 12px 16px;
  }

  .account-main {
    align-items: flex-start;
    padding: 16px 16px 32px;
  }
}
</style>
