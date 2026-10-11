<script setup lang="ts">
import type { ForgeConnection } from '@/cx_types'

import { t } from '@/i18n'

// 由平台托管的项目（forgejo）的「代码仓库」那一块：一行状态。
// 拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 托管服务是建项目时定下来的，所以这一块没有可编的东西：它只把当前状态画出来。
//
// 不给去 Forgejo 的链接：仓库挂在平台替项目建的账号下，人在那边没有账号，点过去只会
// 落在一张登不进去的登录页上。人看代码和改动在面板里（任务的「改动」），托管只是存放
// 和记录（docs/manual/dev/forge.md）。
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
      </div>
      <p class="t-body c-faint mt-2 settings-hint">{{ t('work.projectSettings.forge.hint') }}</p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
