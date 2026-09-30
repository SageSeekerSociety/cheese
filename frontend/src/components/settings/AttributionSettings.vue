<script setup lang="ts">
// 提交署名：把任务请求者列为共同作者。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 三档（跟随系统默认 / 开启 / 关闭）是页面算好的两项，这一件只画；保存时把选中的
// 那一档报上去，落库的映射（default → null、on → true、off → false）在
// `composables/useProjectSettings.ts`。
import { t } from '@/i18n'

defineOptions({ name: 'AttributionSettings' })

defineProps<{
  /** 当前那一档：'default' | 'on' | 'off'。 */
  choice: string
  /** 三档的名字；「跟随系统默认」那一档的名字里带着部署的默认值。 */
  items: { title: string; value: string }[]
  /** 正在保存（下拉禁用并转圈）。 */
  saving: boolean
  /** 上一次保存失败那句话。 */
  error: string | null
  /** 这一档落下去之后，提交上实际生效的是什么。 */
  effective: boolean
}>()

const emit = defineEmits<{
  save: [choice: string]
}>()
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
        :model-value="choice"
        :items="items"
        :label="t('work.projectSettings.attribution.label')"
        :loading="saving"
        :disabled="saving"
        hide-details
        @update:model-value="emit('save', $event)"
      />
      <p class="t-body c-muted mt-2">
        {{ effective ? t('work.projectSettings.attribution.on') : t('work.projectSettings.attribution.off') }}
      </p>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.attribution.hint') }}
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
