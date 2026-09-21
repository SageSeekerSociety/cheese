<template>
  <div class="real-name-info">
    <div class="d-flex justify-space-between align-center mb-6">
      <div class="d-flex align-center">
        <h2 class="text-h5 font-weight-medium mb-0">{{ t('users.privacy.realNameInfo.title') }}</h2>
        <v-chip class="ml-3" size="small" color="primary" variant="outlined">
          <v-icon start icon="mdi-eye-off-outline" size="small"></v-icon>
          {{ t('users.privacy.realNameInfo.masked') }}
        </v-chip>
      </div>
      <v-chip
        :color="hasRealNameInfo ? 'success' : 'warning'"
        variant="tonal"
        size="small"
        class="status-chip"
        :prepend-icon="hasRealNameInfo ? 'mdi-check-circle' : 'mdi-alert-circle'"
      >
        {{ hasRealNameInfo ? t('users.privacy.realNameInfo.verified') : t('users.privacy.realNameInfo.unverified') }}
      </v-chip>
    </div>

    <p class="text-body-2 text-medium-emphasis mb-6">
      {{ t('users.privacy.realNameInfo.intro') }}
    </p>

    <!-- 信息卡片 -->
    <v-card v-if="!loading" class="info-card mb-8" rounded="lg" variant="flat">
      <v-card-text class="pb-0">
        <v-row>
          <!-- 左侧：实名信息内容 -->
          <v-col cols="12" md="8">
            <div class="d-flex align-start mb-6">
              <v-icon icon="mdi-shield-lock" size="24" color="primary" class="mr-3 mt-1"></v-icon>
              <div>
                <div class="text-subtitle-1 font-weight-medium">{{ t('users.privacy.realNameInfo.cardTitle') }}</div>
                <div class="text-caption text-medium-emphasis">{{ t('users.privacy.realNameInfo.cardHint') }}</div>
              </div>
            </div>

            <v-row>
              <v-col cols="12" sm="6">
                <div class="info-field mb-4">
                  <div class="text-caption text-medium-emphasis mb-1">{{ t('users.settings.realName.realName') }}</div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-account" size="small" color="primary" class="mr-2"></v-icon>
                    <span class="text-body-1 font-weight-medium">{{
                      realNameInfo?.realName || t('users.privacy.realNameInfo.notProvided')
                    }}</span>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" sm="6">
                <div class="info-field mb-4">
                  <div class="text-caption text-medium-emphasis mb-1">{{ t('users.settings.realName.studentId') }}</div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-card-account-details" size="small" color="primary" class="mr-2"></v-icon>
                    <span class="text-body-1 font-weight-medium">{{
                      realNameInfo?.studentId || t('users.privacy.realNameInfo.notProvided')
                    }}</span>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" sm="4">
                <div class="info-field mb-4">
                  <div class="text-caption text-medium-emphasis mb-1">{{ t('users.settings.realName.grade') }}</div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-school" size="small" color="primary" class="mr-2"></v-icon>
                    <span class="text-body-1 font-weight-medium">{{
                      realNameInfo?.grade || t('users.privacy.realNameInfo.notProvided')
                    }}</span>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" sm="4">
                <div class="info-field mb-4">
                  <div class="text-caption text-medium-emphasis mb-1">{{ t('users.settings.realName.major') }}</div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-book-education" size="small" color="primary" class="mr-2"></v-icon>
                    <span class="text-body-1 font-weight-medium">{{
                      realNameInfo?.major || t('users.privacy.realNameInfo.notProvided')
                    }}</span>
                  </div>
                </div>
              </v-col>

              <v-col cols="12" sm="4">
                <div class="info-field mb-4">
                  <div class="text-caption text-medium-emphasis mb-1">{{ t('users.settings.realName.className') }}</div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-account-group" size="small" color="primary" class="mr-2"></v-icon>
                    <span class="text-body-1 font-weight-medium">{{
                      realNameInfo?.className || t('users.privacy.realNameInfo.notProvided')
                    }}</span>
                  </div>
                </div>
              </v-col>
            </v-row>
          </v-col>

          <!-- 右侧：信息安全状态卡片 -->
          <v-col cols="12" md="4">
            <v-card class="status-info-card h-100" rounded="lg" color="surface-light" variant="flat">
              <v-card-text>
                <div class="d-flex flex-column justify-space-between h-100">
                  <div>
                    <div class="text-subtitle-2 font-weight-medium mb-4">
                      {{ t('users.privacy.realNameInfo.securityStatus') }}
                    </div>

                    <div class="status-item d-flex align-start mb-3">
                      <v-avatar size="24" color="success" class="mr-2">
                        <v-icon icon="mdi-lock" size="14"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-body-2 font-weight-medium">
                          {{ t('users.privacy.realNameInfo.encryptedTitle') }}
                        </div>
                        <div class="text-caption text-medium-emphasis">
                          {{ t('users.privacy.realNameInfo.encryptedDesc') }}
                        </div>
                      </div>
                    </div>

                    <div class="status-item d-flex align-start mb-3">
                      <v-avatar size="24" color="success" class="mr-2">
                        <v-icon icon="mdi-account-key" size="14"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-body-2 font-weight-medium">
                          {{ t('users.privacy.realNameInfo.accessControlTitle') }}
                        </div>
                        <div class="text-caption text-medium-emphasis">
                          {{ t('users.privacy.realNameInfo.accessControlDesc') }}
                        </div>
                      </div>
                    </div>

                    <div class="status-item d-flex align-start">
                      <v-avatar size="24" color="success" class="mr-2">
                        <v-icon icon="mdi-history" size="14"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-body-2 font-weight-medium">
                          {{ t('users.privacy.realNameInfo.accessLogTitle') }}
                        </div>
                        <div class="text-caption text-medium-emphasis">
                          {{ t('users.privacy.realNameInfo.accessLogDesc') }}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div class="mt-4">
                    <v-btn
                      :to="{ name: 'PrivacyCenterAccessLogs' }"
                      color="primary"
                      variant="text"
                      size="small"
                      block
                      class="mb-2"
                    >
                      {{ t('users.privacy.realNameInfo.viewAccessLog') }}
                    </v-btn>
                  </div>
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </v-card-text>

      <v-divider class="my-4"></v-divider>

      <v-card-actions class="px-4 pb-4">
        <v-btn color="primary" variant="flat" :to="{ name: 'UserSettingsRealName' }" prepend-icon="mdi-pencil">
          {{ t('users.privacy.realNameInfo.editRealName') }}
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="text" prepend-icon="mdi-eye" :to="{ name: 'PrivacyCenterAccessLogs' }">
          {{ t('users.privacy.realNameInfo.viewAccessLog') }}
        </v-btn>
      </v-card-actions>
    </v-card>

    <!-- 加载状态 -->
    <v-card v-else class="loading-card mb-8" rounded="lg" variant="flat">
      <v-card-text class="d-flex justify-center align-center py-8">
        <v-progress-circular indeterminate color="primary" class="mr-3"></v-progress-circular>
        <span>{{ t('users.privacy.realNameInfo.loading') }}</span>
      </v-card-text>
    </v-card>

    <!-- 实名信息使用场景说明（整合了原对话框内容） -->
    <v-card class="usage-card" variant="flat" rounded="lg">
      <v-card-text>
        <div class="d-flex align-start mb-4">
          <v-icon icon="mdi-shield-lock-outline" size="24" color="primary" class="mr-3 mt-1"></v-icon>
          <div>
            <div class="text-subtitle-1 font-weight-medium">{{ t('users.privacy.realNameInfo.usageTitle') }}</div>
            <div class="text-caption text-medium-emphasis">{{ t('users.privacy.realNameInfo.usageSubtitle') }}</div>
          </div>
        </div>

        <!-- 使用场景说明 -->
        <div class="mb-6">
          <h3 class="text-subtitle-2 font-weight-medium mb-3">{{ t('users.privacy.realNameInfo.scenariosTitle') }}</h3>
          <v-row>
            <v-col cols="12" md="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-account-check" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.scenario1Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.scenario1Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6" md="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-certificate" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.scenario2Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.scenario2Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6" md="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-trophy" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.scenario3Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.scenario3Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6" md="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-school" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.scenario4Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.scenario4Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>
        </div>

        <!-- 隐私保护说明（从原对话框整合） -->
        <v-divider class="my-5"></v-divider>

        <div class="mb-4">
          <h3 class="text-subtitle-2 font-weight-medium mb-3">{{ t('users.privacy.realNameInfo.protectionTitle') }}</h3>
          <p class="text-body-2 mb-4">
            {{ t('users.privacy.realNameInfo.protectionIntro') }}
          </p>

          <v-row>
            <v-col cols="12" sm="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-incognito" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.protection1Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.protection1Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-key-variant" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.protection2Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.protection2Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-file-document-outline" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.protection3Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.protection3Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" sm="6">
              <v-card class="usage-scenario-card h-100" variant="flat" rounded="lg">
                <v-card-text>
                  <div class="d-flex flex-column h-100">
                    <v-avatar size="42" class="primary-soft mb-3" rounded>
                      <v-icon icon="mdi-eye-off-outline" size="24" color="primary"></v-icon>
                    </v-avatar>
                    <div class="text-subtitle-2 font-weight-medium mb-1">
                      {{ t('users.privacy.realNameInfo.protection4Title') }}
                    </div>
                    <p class="text-body-2 text-medium-emphasis flex-grow-1">
                      {{ t('users.privacy.realNameInfo.protection4Desc') }}
                    </p>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>
        </div>

        <div class="d-flex align-center bg-surface-light pa-3 rounded">
          <v-icon icon="mdi-information-outline" color="primary" class="mr-2"></v-icon>
          <p class="text-body-2 mb-0">
            <i18n-t keypath="users.privacy.realNameInfo.footerNote" scope="global" tag="span">
              <template #accessLog>
                <router-link :to="{ name: 'PrivacyCenterAccessLogs' }" class="text-decoration-none">
                  {{ t('users.privacy.realNameInfo.footerAccessLog') }}
                </router-link>
              </template>
            </i18n-t>
          </p>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>

<script lang="ts" setup>
import type { RealNameInfo } from '@/network/api/users/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { UserApi } from '@/network/api/users'
import { currentUserId } from '@/services/account'

const { t } = useI18n()

const loading = ref(false)
const hasRealNameInfo = ref(false)
const realNameInfo = ref<RealNameInfo | undefined>()

// 获取实名信息
const fetchRealNameInfo = async () => {
  if (!currentUserId.value) return

  loading.value = true
  try {
    const { data } = await UserApi.getRealNameInfo(currentUserId.value, false)
    hasRealNameInfo.value = data.hasIdentity
    realNameInfo.value = data.identity
  } catch (error: any) {
    console.error('获取实名信息失败', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchRealNameInfo()
})
</script>

<style scoped lang="scss">
.real-name-info {
  .status-chip {
    font-weight: 500;
  }

  .info-card {
    background-color: var(--surface);
    border: 1px solid rgba(var(--v-border-color), 0.12);
  }

  .status-info-card {
    border: 1px solid rgba(var(--v-border-color), 0.12);
  }

  .usage-card {
    background-color: var(--surface);
    border: 1px solid rgba(var(--v-border-color), 0.12);
  }

  .usage-scenario-card {
    transition: all 0.2s ease;
    border: 1px solid rgba(var(--v-border-color), 0.12);
    background-color: var(--surface);

    &:hover {
      border-color: rgba(var(--v-theme-primary), 0.15);
      box-shadow: 0 2px 8px rgba(var(--v-theme-primary), 0.05);
    }
  }

  .info-field {
    position: relative;

    &::after {
      content: '';
      position: absolute;
      bottom: -8px;
      left: 0;
      width: 100%;
      height: 1px;
      background: linear-gradient(to right, rgba(var(--v-theme-primary), 0.08), transparent);
    }
  }

  .primary-soft {
    background-color: rgba(var(--v-theme-primary), 0.08);
  }

  .privacy-feature-card {
    height: 100%;
    padding: 16px;
    border-radius: 8px;
    border: 1px solid rgba(var(--v-border-color), 0.12);
    background-color: var(--surface);
    transition: all 0.2s ease;

    &:hover {
      border-color: rgba(var(--v-theme-primary), 0.15);
      box-shadow: 0 2px 8px rgba(var(--v-theme-primary), 0.05);
    }
  }

  .data-info {
    display: inline-block;
    margin-top: 4px;
    padding: 2px 0;
    font-style: italic;
    font-weight: 500;
    color: rgb(var(--v-theme-primary));
  }
}
</style>
