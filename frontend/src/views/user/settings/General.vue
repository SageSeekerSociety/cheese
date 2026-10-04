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
        <div class="general__action">
          <SaveStatus :saving="saving" :saved="saved" :error="error" :failed-text="t('account.general.saveFailed')" />
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
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import SaveStatus from '@/components/base/SaveStatus.vue'
import { t } from '@/i18n'
import { desktopOpensAtLogin, setDesktopOpensAtLogin } from '@/lib/desktopApp'

// null until the app has said: the switch does not guess.
const opensAtLogin = ref<boolean | null>(null)

// 保存结果就地回执（§3.11）：开关旁边那一行，不再只靠按钮转圈。
// 系统拒绝时只说这一句固定的话，不把底层那句（可能是技术细节）端给人看。
const { saving, saved, error, run } = useSaveState({
  feedback: 'inline',
  messages: { failed: t('account.general.saveFailed') },
  describeError: () => t('account.general.saveFailed'),
})

onMounted(async () => {
  opensAtLogin.value = await desktopOpensAtLogin().catch(() => false)
})

async function change(on: boolean | null) {
  if (on === null || saving.value) return
  // Moved with the hand first, so that putting it back on a refusal is a change
  // the switch is redrawn for, not a value it already had.
  const previous = opensAtLogin.value
  opensAtLogin.value = on
  await run(async () => {
    try {
      await setDesktopOpensAtLogin(on)
    } catch (e) {
      // Put it back on a refusal: that is a change the switch is redrawn for,
      // not a value it already had.
      opensAtLogin.value = previous
      throw e
    }
  })
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.general__action {
  display: flex;
  gap: 12px;
  align-items: center;
}
</style>
