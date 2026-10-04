<template>
  <div>
    <!-- 信息保护卡片 -->
    <v-card class="mb-5 privacy-protection-card" variant="flat" rounded="lg">
      <v-card-text class="pa-0">
        <v-row>
          <v-col cols="12" md="6">
            <div class="d-flex align-start pa-3">
              <v-avatar size="36" class="primary-soft mr-3">
                <v-icon icon="mdi-incognito" size="20" color="primary"></v-icon>
              </v-avatar>
              <div>
                <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.anonymousTitle') }}</div>
                <p class="text-body-2 text-medium-emphasis mb-0">
                  {{ t('tasks.privacyInfo.anonymousBody') }}
                </p>
              </div>
            </div>
          </v-col>

          <v-col cols="12" md="6">
            <div class="d-flex align-start pa-3">
              <v-avatar size="36" class="primary-soft mr-3">
                <v-icon icon="mdi-key-variant" size="20" color="primary"></v-icon>
              </v-avatar>
              <div>
                <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.encryptionTitle') }}</div>
                <p class="text-body-2 text-medium-emphasis mb-0">
                  {{ t('tasks.privacyInfo.encryptionBody') }}
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
                <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.purposeTitle') }}</div>
                <p class="text-body-2 text-medium-emphasis mb-0">
                  {{ t('tasks.privacyInfo.purposeBody') }}
                </p>
              </div>
            </div>
          </v-col>

          <v-col cols="12" md="6">
            <div class="d-flex align-start pa-3">
              <v-avatar size="36" class="primary-soft mr-3">
                <v-icon icon="mdi-eye-off-outline" size="20" color="primary"></v-icon>
              </v-avatar>
              <div>
                <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.isolationTitle') }}</div>
                <p class="text-body-2 text-medium-emphasis mb-0">{{ t('tasks.privacyInfo.isolationBody') }}</p>
              </div>
            </div>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 使用场景 -->
    <div class="mb-4">
      <div class="text-subtitle-2 font-weight-medium mb-3">{{ t('tasks.privacyInfo.scenariosTitle') }}</div>
      <v-card variant="flat" rounded="lg" class="privacy-usage-card mb-3">
        <v-card-text class="pa-3">
          <div class="d-flex align-start">
            <v-avatar size="36" class="primary-soft mr-3">
              <v-icon icon="mdi-account-check" size="20" color="primary"></v-icon>
            </v-avatar>
            <div>
              <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.verificationTitle') }}</div>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('tasks.privacyInfo.verificationBody') }}</p>
            </div>
          </div>
        </v-card-text>
      </v-card>

      <v-card variant="flat" rounded="lg" class="privacy-usage-card mb-3">
        <v-card-text class="pa-3">
          <div class="d-flex align-start">
            <v-avatar size="36" class="primary-soft mr-3">
              <v-icon icon="mdi-bell-outline" size="20" color="primary"></v-icon>
            </v-avatar>
            <div>
              <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.eligibilityTitle') }}</div>
              <p class="text-body-2 text-medium-emphasis mb-0">
                {{ t('tasks.privacyInfo.eligibilityBody') }}
              </p>
            </div>
          </div>
        </v-card-text>
      </v-card>

      <v-card variant="flat" rounded="lg" class="privacy-usage-card">
        <v-card-text class="pa-3">
          <div class="d-flex align-start">
            <v-avatar size="36" class="primary-soft mr-3">
              <v-icon icon="mdi-certificate-outline" size="20" color="primary"></v-icon>
            </v-avatar>
            <div>
              <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.certificationTitle') }}</div>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('tasks.privacyInfo.certificationBody') }}</p>
            </div>
          </div>
        </v-card-text>
      </v-card>
    </div>

    <!-- 用户权利 -->
    <v-alert type="info" variant="tonal" class="privacy-rights-alert mb-3" border="start" density="comfortable">
      <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.privacyInfo.rightsTitle') }}</div>
      <p class="text-body-2 mb-0">
        {{ t('tasks.privacyInfo.rightsBody') }}
      </p>
    </v-alert>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
// 无需额外逻辑
</script>

<style scoped>
.primary-soft {
  background-color: rgba(var(--v-theme-primary), 0.08);
}

.privacy-protection-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
}

.privacy-usage-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: var(--surface);
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;
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
