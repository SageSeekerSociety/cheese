<script setup lang="ts">
import type { CallbackNotice } from '@/composables/useProjectSettings'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// 平台托管的项目迁到自己的 GitHub 仓库：一行地址输入框和「迁到 GitHub」。
// 迁移本身（授权、推送、换绑定）在 `composables/useProjectSettings.ts` 和后端
// `app/api/github_migration.py`。只有平台托管、仓库已经备好的项目看得到这一块，
// 挂不挂由页面决定。领题建出的项目都是平台托管，学生靠这一块把作业放到 GitHub。
defineOptions({ name: 'GithubMigrateSettings' })

defineProps<{
  /** 输入框里那段地址。 */
  url: string
  /** 正在迁（按钮转圈）：推送在请求里跑，可能要一会儿。 */
  moving: boolean
  /** 迁移那一条结果。 */
  notice: CallbackNotice | null
}>()

const emit = defineEmits<{
  'update:url': [value: string]
  move: []
  'clear-notice': []
}>()
</script>

<template>
  <section class="page-section" data-testid="github-migrate">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-github</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.githubMigrate.title') }}</span>
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
      <div class="d-flex align-center" style="gap: 8px">
        <v-text-field
          :model-value="url"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :disabled="moving"
          :placeholder="t('work.projectSettings.githubMigrate.placeholder')"
          style="flex: 1"
          @update:model-value="emit('update:url', $event)"
          @keydown.enter="url.trim() && emit('move')"
        />
        <BaseButton kind="primary" size="sm" :loading="moving" :disabled="!url.trim()" @click="emit('move')">
          {{ t('work.projectSettings.githubMigrate.submit') }}
        </BaseButton>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.githubMigrate.hint') }}
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
