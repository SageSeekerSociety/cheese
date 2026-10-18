<script setup lang="ts">
// 提交署名：这个项目的 PR 和提交算谁的。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 两种：任务负责人（默认），或者 AI 队友。选中哪种、存没存上由页面管，这一件只画、
// 只把选中的那一种报上去。
import type { CommitCreditMode } from '@/cx_types'

import { t } from '@/i18n'

defineOptions({ name: 'AttributionSettings' })

defineProps<{
  /** 当前那一种；还没读到时为 null。 */
  mode: CommitCreditMode | null
  /** 项目里给 AI 队友起的名字。 */
  agentName: string
  /** 正在保存（下拉禁用并转圈）。 */
  saving: boolean
  /** 上一次保存失败那句话。 */
  error: string | null
}>()

const emit = defineEmits<{
  save: [mode: CommitCreditMode]
}>()

const items = [
  { title: t('work.projectSettings.attribution.owner'), value: 'owner' },
  { title: t('work.projectSettings.attribution.agent'), value: 'agent' },
]
</script>

<template>
  <section class="page-section" data-testid="forge-attribution">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-account-edit-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.attribution.title') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" density="compact" class="mb-3">{{ error }}</v-alert>
      <v-select
        autocomplete="off"
        :model-value="mode"
        :items="items"
        :label="t('work.projectSettings.attribution.label')"
        :loading="saving"
        :disabled="saving"
        hide-details
        @update:model-value="emit('save', $event)"
      />
      <p v-if="mode" class="t-body c-muted mt-2">
        {{
          mode === 'owner'
            ? t('work.projectSettings.attribution.ownerHint', { agent: agentName })
            : t('work.projectSettings.attribution.agentHint', { agent: agentName })
        }}
      </p>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.attribution.hint') }}
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
