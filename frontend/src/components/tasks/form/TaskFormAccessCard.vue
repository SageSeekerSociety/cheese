<script setup lang="ts">
// 权限设置那张卡：一个开关，开了之后才出现的「哪些域名组能参与」多选，以及一个可选
// 域名组都没有时替它站岗的那句提醒。
//
// 它只画：开关和多选的值都是 props 进来的，改一次往上报一次。域名组那张表也是算好了
// 传进来的（`{ title, value, subtitle }`），这一件不认 `DomainGroup`。
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
type FieldControl = Record<string, any>

defineProps<{
  /** 域名组的选项（外面从 `domainGroups` 映射好，带一句域名清单当副标题）。 */
  domainGroupItems: { title: string; value: number; subtitle: string }[]
  accessControlEnabledControl: FieldControl
  accessDomainGroupIdsControl: FieldControl
}>()

const accessControlEnabled = defineModel<boolean | undefined>('accessControlEnabled', { required: true })
const accessDomainGroupIds = defineModel<number[] | undefined>('accessDomainGroupIds', { required: true })

const { t } = useI18n()
</script>

<template>
  <TaskFormSection icon="mdi-shield-lock-outline" :title="t('tasks.form.accessControl.title')">
    <v-switch v-model="accessControlEnabled" color="primary" hide-details v-bind="accessControlEnabledControl">
      <template #label>
        <div class="d-flex align-center">
          <v-icon
            :icon="accessControlEnabled ? 'mdi-shield-check' : 'mdi-shield-outline'"
            :color="accessControlEnabled ? 'primary' : 'medium-emphasis'"
            class="mr-2"
          ></v-icon>
          <span>{{ t('tasks.form.accessControl.enableAccessRestriction') }}</span>
          <v-tooltip location="top">
            <template #activator="{ props: tooltipProps }">
              <v-icon size="small" color="primary" class="ml-2" v-bind="tooltipProps">mdi-information-outline</v-icon>
            </template>
            <span>{{ t('tasks.form.accessControl.enableAccessRestrictionHint') }}</span>
          </v-tooltip>
        </div>
      </template>
    </v-switch>

    <v-row v-if="accessControlEnabled" class="mt-4">
      <v-col cols="12">
        <v-select
          v-if="domainGroupItems.length > 0"
          v-model="accessDomainGroupIds"
          autocomplete="off"
          :items="domainGroupItems"
          :label="t('tasks.form.accessControl.domainGroups')"
          :hint="t('tasks.form.accessControl.domainGroupsHint')"
          chips
          multiple
          persistent-hint
          v-bind="accessDomainGroupIdsControl"
          item-title="title"
          item-value="value"
        >
          <template #prepend-inner>
            <v-icon size="small" color="primary">mdi-domain</v-icon>
          </template>
          <template #item="{ item, props: slotProps }">
            <v-list-item v-bind="slotProps">
              <template #prepend>
                <v-icon icon="mdi-web" color="primary" class="mr-2"></v-icon>
              </template>
              <template v-if="item.raw" #subtitle>
                <span class="text-caption text-medium-emphasis">{{ item.raw.subtitle }}</span>
              </template>
            </v-list-item>
          </template>
          <template #chip="{ item, props: chipProps }">
            <v-chip v-bind="chipProps" color="primary" variant="tonal" size="small">
              <template #prepend>
                <v-icon start size="x-small">mdi-web</v-icon>
              </template>
              {{ item.title }}
            </v-chip>
          </template>
        </v-select>
        <v-alert
          v-else
          color="warning"
          variant="tonal"
          density="comfortable"
          border="start"
          icon="mdi-alert-circle-outline"
        >
          {{ t('tasks.form.accessControl.noDomainGroups') }}
        </v-alert>
      </v-col>
    </v-row>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
