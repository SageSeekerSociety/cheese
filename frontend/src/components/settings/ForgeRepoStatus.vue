<script setup lang="ts">
import type { ForgeConnection } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// 由平台托管的项目（forgejo）的「代码仓库」那一块：一行状态加一颗「打开仓库」。
// 拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 托管服务是建项目时定下来的，所以这一块没有可编的东西：它只把当前状态画出来。
defineOptions({ name: 'ForgeRepoStatus' })

defineProps<{
  /** `kind === 'forgejo'` 的那份仓库状态。 */
  forge: ForgeConnection
}>()
</script>

<template>
  <section class="page-section" data-testid="forge-repository">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.forge.title') }}</span>
    </div>
    <div class="page-section-body">
      <div class="d-flex align-center flex-wrap" style="gap: 8px">
        <v-icon v-if="forge.connected" size="18" color="success">mdi-check-circle</v-icon>
        <span class="t-body">{{
          forge.connected ? t('work.projectSettings.forge.hosted') : t('work.projectSettings.forge.preparing')
        }}</span>
        <v-spacer />
        <BaseButton
          v-if="forge.url"
          kind="secondary"
          :href="forge.url"
          target="_blank"
          rel="noopener noreferrer"
          size="sm"
        >
          {{ t('work.projectSettings.forge.open') }}
        </BaseButton>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">{{ t('work.projectSettings.forge.hint') }}</p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
