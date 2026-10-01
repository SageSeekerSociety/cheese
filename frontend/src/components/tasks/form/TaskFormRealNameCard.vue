<script setup lang="ts">
import { useI18n } from 'vue-i18n'

// 实名信息要求那张卡：一个开关，加上开关底下跟着变的那一块（开了说开了什么、没开说
// 没开意味着什么）。
//
// 它只画：开关的值是 props 进来的，掰一下往上报一次。至于「开了之后再提交要不要先
// 弹那段隐私说明」，那是提交那条路上的事（`useTaskForm.ts` 的第一道闸门），跟这张
// 卡无关 —— 所以这一件不知道有弹窗。
import TaskFormSection from './TaskFormSection.vue'

const { t } = useI18n()

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
type FieldControl = Record<string, any>

defineProps<{
  requireRealNameControl: FieldControl
}>()

const requireRealName = defineModel<boolean | undefined>('requireRealName', { required: true })
</script>

<template>
  <TaskFormSection icon="mdi-shield-account" :title="t('tasks.form.realNameTitle')">
    <v-switch v-model="requireRealName" color="primary" hide-details v-bind="requireRealNameControl">
      <template #label>
        <div class="d-flex align-center">
          <v-icon
            :icon="requireRealName ? 'mdi-account-check' : 'mdi-account-outline'"
            :color="requireRealName ? 'primary' : 'medium-emphasis'"
            class="mr-2"
          ></v-icon>
          <span>{{ t('tasks.form.requireRealName') }}</span>
          <v-tooltip location="top">
            <template #activator="{ props }">
              <v-icon size="small" color="primary" class="ml-2" v-bind="props">mdi-information-outline</v-icon>
            </template>
            <span>{{ t('tasks.form.requireRealNameHint') }}</span>
          </v-tooltip>
        </div>
      </template>
    </v-switch>

    <v-alert
      v-if="requireRealName"
      color="primary"
      variant="tonal"
      class="mt-3 mb-0"
      density="comfortable"
      border="start"
    >
      <div class="d-flex align-start">
        <v-avatar color="primary" class="mr-3 mt-1" size="28">
          <!-- 琥珀底上的反白图标：surface 在深色下是深墨，white 会糊在 #FFA733 上 -->
          <v-icon icon="mdi-shield-check" color="surface" size="18"></v-icon>
        </v-avatar>
        <div>
          <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.realNameChosen.title') }}</div>
          <p class="text-body-2 mb-0">
            • {{ t('tasks.form.realNameChosen.line1') }}<br />
            • {{ t('tasks.form.realNameChosen.line2') }}<br />
            • {{ t('tasks.form.realNameChosen.line3') }}
          </p>
        </div>
      </div>
    </v-alert>

    <div v-else class="d-flex align-center mt-3">
      <v-icon color="medium-emphasis" icon="mdi-information-outline" class="mr-2"></v-icon>
      <span class="text-body-2 text-medium-emphasis">{{ t('tasks.form.realNameNotRequired') }}</span>
    </div>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
