<!--
  「通用」：这台电脑上的桌面 app 自己的设置，只在 app 里出现（sections.ts）。
  它们存在这台电脑上，不跟着账号走，所以页头先说清楚。
-->
<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('account.settings.general') }}</h1>
      <p class="settings-page__lede">{{ t('account.general.lede') }}</p>
    </header>

    <section class="settings-card">
      <div class="srow">
        <label class="srow__k" for="general-open-at-login">{{ t('account.general.openAtLogin') }}</label>
        <span class="srow__v">{{ t('account.general.openAtLoginHint') }}</span>
        <v-switch
          id="general-open-at-login"
          :model-value="opensAtLogin"
          :loading="saving"
          :disabled="opensAtLogin === null || saving"
          color="primary"
          density="compact"
          inset
          hide-details
          @update:model-value="change"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { desktopOpensAtLogin, setDesktopOpensAtLogin } from '@/lib/desktopApp'

// null until the app has said: the switch does not guess.
const opensAtLogin = ref<boolean | null>(null)
const saving = ref(false)

onMounted(async () => {
  opensAtLogin.value = await desktopOpensAtLogin().catch(() => false)
})

async function change(on: boolean | null) {
  if (on === null || saving.value) return
  // Moved with the hand first, so that putting it back on a refusal is a change
  // the switch is redrawn for, not a value it already had.
  opensAtLogin.value = on
  saving.value = true
  try {
    await setDesktopOpensAtLogin(on)
  } catch {
    opensAtLogin.value = !on
    toast.error(t('account.general.saveFailed'))
  } finally {
    saving.value = false
  }
}
</script>

<style scoped src="./settings-card.css"></style>
