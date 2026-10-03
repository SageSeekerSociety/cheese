<script setup lang="ts">
// 上游仓库 (spec §6.3)：接一个已有的仓库、把它的历史拉进来。只画那一行输入框和
// 「保存」，保存与解绑在 `composables/useProjectSettings.ts`。拆自
// `views/ProjectSettingsView.vue`（#2143）。
//
// 只有还没接上仓库的 GitHub 项目看得到这一块 —— 那个条件（项目的仓库形态）是页面的
// 判断，所以由页面决定挂不挂它。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'UpstreamRepoSettings' })

defineProps<{
  /** 输入框里那段字（保存成功之后是后端回话的规范化地址）。 */
  url: string
  saving: boolean
}>()

const emit = defineEmits<{
  'update:url': [value: string]
  save: []
}>()
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.upstream.title') }}</span>
    </div>
    <div class="page-section-body">
      <div class="d-flex align-center" style="gap: 8px">
        <v-text-field
          :model-value="url"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :placeholder="t('work.projectSettings.upstream.placeholder')"
          style="flex: 1"
          @update:model-value="emit('update:url', $event)"
          @keydown.enter="emit('save')"
        />
        <BaseButton kind="primary" size="sm" :loading="saving" @click="emit('save')">
          {{ t('work.projectSettings.upstream.save') }}
        </BaseButton>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.upstream.hint') }}
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
