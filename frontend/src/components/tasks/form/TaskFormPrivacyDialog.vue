<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'

const { t } = useI18n()

// 「实名信息隐私保护」那段说明：出题人第一次要求实名信息、要交卷时弹出来，读完点
// 「了解并接受」才真的交上去。
//
// 它只画这一段说明。要不要弹、弹完之后那份提交怎么办，全在 `useTaskForm.ts` 里
// （第一道闸门）—— 这里只把「人点的是哪一个」报出去（`confirm` / `cancel`）。
//
// `open` 是 `v-model:open`：关掉的时候（人按 Esc 也不行，它是 `persistent` 的）由
// 容器把状态收回去，所以这一件自己没有状态。
const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <v-dialog v-model="open" :max-width="DIALOG_WIDTH.md" persistent scrollable>
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center px-4 pt-4 pb-2">
        <v-icon color="primary" class="mr-3" size="28">mdi-shield-check</v-icon>
        <span class="text-h5 font-weight-medium">{{ t('tasks.form.privacy.title') }}</span>
      </v-card-title>

      <v-card-text class="px-4 pb-2">
        <p class="text-subtitle-2 font-weight-medium mb-4">
          {{ t('tasks.form.privacy.intro') }}
        </p>

        <!-- 信息保护卡片 -->
        <v-card class="mb-5 privacy-protection-card" variant="flat" rounded="lg">
          <v-card-text class="pa-0">
            <v-row>
              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-eye-off" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('tasks.form.privacy.anonymousTitle') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      {{ t('tasks.form.privacy.anonymousBody') }}
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-file-document-outline" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('tasks.form.privacy.purposeTitle') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      {{ t('tasks.form.privacy.purposeBody') }}
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-shield-lock" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('tasks.form.privacy.encryptionTitle') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      {{ t('tasks.form.privacy.encryptionBody') }}
                    </p>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" md="6">
                <div class="d-flex align-start pa-3">
                  <v-avatar size="36" class="primary-soft mr-3">
                    <v-icon icon="mdi-history" size="20" color="primary"></v-icon>
                  </v-avatar>
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.privacy.accessTitle') }}</div>
                    <p class="text-body-2 text-medium-emphasis mb-0">
                      {{ t('tasks.form.privacy.accessBody') }}
                    </p>
                  </div>
                </div>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>

        <!-- 使用场景 -->
        <div class="mb-4">
          <div class="text-subtitle-2 font-weight-medium mb-3">{{ t('tasks.form.privacy.scenariosTitle') }}</div>
          <v-row dense>
            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-account-check" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.verificationTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.verificationBody') }}
                      </p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-certificate-outline" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.certificationTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.certificationBody') }}
                      </p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="4">
              <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                <v-card-text class="pa-3">
                  <div class="d-flex align-start h-100">
                    <v-avatar size="36" class="primary-soft mr-3 mt-1">
                      <v-icon icon="mdi-trophy" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.awardsTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">{{ t('tasks.form.privacy.awardsBody') }}</p>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>
        </div>

        <!-- 合规承诺 -->
        <v-alert type="info" variant="tonal" class="privacy-rights-alert mb-3" border="start" density="comfortable">
          <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.privacy.commitmentTitle') }}</div>
          <p class="text-body-2 mb-0">
            {{ t('tasks.form.privacy.commitmentBody') }}
          </p>
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-2">
        <v-spacer></v-spacer>
        <BaseButton kind="ghost" @click="emit('cancel')">{{ t('global.cancel') }}</BaseButton>
        <BaseButton kind="primary" @click="emit('confirm')">{{ t('tasks.form.privacy.understood') }}</BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.privacy-protection-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
}

.privacy-usage-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: var(--surface);
  transition: all 0.2s ease;
}

.privacy-usage-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  background-color: rgba(var(--v-theme-primary), 0.01);
}

.privacy-rights-alert {
  background-color: rgba(var(--v-theme-info), 0.05);
  border-color: rgba(var(--v-theme-info), 0.3);
}
</style>

<style scoped src="./task-form.css"></style>
