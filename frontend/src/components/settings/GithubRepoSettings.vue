<script setup lang="ts">
import type { CallbackNotice } from '@/composables/useProjectSettings'
import type { ForgeConnection } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// 连接 GitHub 仓库 (#192)：cheesex-app 装到具体仓库上，之后这个项目的 git 操作走这个
// installation 的短时 token。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// **结果只报在自己这一块**：`notice` 是仓库流程那一条，不是和「连接 GitHub 账号」
// 共用的那一条 —— 两条流程各有各的按钮，一次动作的结果出现在另一件事的标题下面
// 就是撒谎（`githubSettingsSections.spec.ts` 钉的就是这件事）。上游仓库地址保存失败
// 也落在这同一条里：两件事说的是「这个项目接在哪个仓库上」。
//
// 点下按钮之后是页面在等后端，所以这里只报「正在连」，不自己判断结果。
defineOptions({ name: 'GithubRepoSettings' })

defineProps<{
  /** `kind === 'github_app'` 的那份仓库状态。 */
  forge: ForgeConnection
  /** 正在连（按钮转圈）。 */
  connecting: boolean
  /** 仓库流程那一条结果（成功 / 失败 / 等待批准）。 */
  notice: CallbackNotice | null
}>()

const emit = defineEmits<{
  connect: []
  'clear-notice': []
}>()
</script>

<template>
  <section class="page-section" data-testid="github-repository">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-github</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.githubRepo.title') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert
        v-if="notice"
        :type="notice.type"
        density="comfortable"
        closable
        class="mb-3"
        @click:close="emit('clear-notice')"
      >
        {{ notice.text }}
      </v-alert>
      <div v-if="forge.connected" class="d-flex align-center flex-wrap" style="gap: 8px">
        <v-icon size="18" color="success">mdi-check-circle</v-icon>
        <span class="t-body settings-row-label">
          {{ t('work.projectSettings.githubRepo.connected', { repo: forge.repo }) }}
        </span>
        <v-spacer />
        <BaseButton kind="secondary" size="sm" :loading="connecting" @click="emit('connect')">
          {{ t('work.projectSettings.githubRepo.reconnect') }}
        </BaseButton>
      </div>
      <div v-else class="d-flex align-center flex-wrap" style="gap: 8px">
        <span class="t-body c-muted">{{ t('work.projectSettings.githubRepo.none') }}</span>
        <v-spacer />
        <BaseButton kind="primary" size="sm" :loading="connecting" @click="emit('connect')">
          {{ t('work.projectSettings.githubRepo.connect') }}
        </BaseButton>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.githubRepo.hint') }}
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
