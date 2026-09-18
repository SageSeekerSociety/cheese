<template>
  <div class="realname-tasks">
    <div class="d-flex justify-space-between align-center mb-6">
      <h2 class="text-h5 font-weight-medium mb-0">{{ t('users.privacy.dataSharing.title') }}</h2>
      <v-chip color="primary" variant="tonal" size="small" class="status-chip">
        <template #prepend>
          <v-icon icon="mdi-clipboard-check" size="small"></v-icon>
        </template>
        {{ t('users.privacy.dataSharing.count', { count: 3 }) }}
      </v-chip>
    </div>

    <p class="text-body-2 text-medium-emphasis mb-6">
      {{ t('users.privacy.dataSharing.intro') }}
    </p>

    <!-- 赛题列表 -->
    <v-card variant="flat" rounded="lg" class="mb-6 tasks-list-card">
      <v-list>
        <v-list-item class="task-item py-4">
          <div class="d-flex w-100">
            <v-avatar class="task-icon mr-4 primary-soft">
              <v-icon icon="mdi-city-variant" size="24" color="primary"></v-icon>
            </v-avatar>

            <div class="flex-grow-1">
              <div class="d-flex justify-space-between align-start mb-2">
                <div>
                  <div class="text-subtitle-1 font-weight-medium">
                    {{ t('users.privacy.dataSharing.contest1Name') }}
                  </div>
                  <div class="text-caption text-medium-emphasis mb-3">
                    {{ t('users.privacy.dataSharing.contest1Host') }}
                  </div>
                </div>
                <v-chip size="small" color="success" variant="tonal">{{
                  t('users.privacy.dataSharing.statusOngoing')
                }}</v-chip>
              </div>

              <div class="d-flex flex-wrap gap-4 mb-3">
                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colSubmittedAt') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-calendar" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest1Date') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colDataUsed') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-card-account-details" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest1Data') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colPurpose') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-information" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest1Purpose') }}</span>
                  </div>
                </div>
              </div>

              <div class="task-description text-body-2 text-medium-emphasis mb-3">
                {{ t('users.privacy.dataSharing.contest1Desc') }}
              </div>

              <div class="d-flex flex-wrap gap-2">
                <v-chip size="small" variant="flat" color="primary">{{
                  t('users.privacy.dataSharing.contest1TagLevel')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest1TagFormat')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest1TagCredit')
                }}</v-chip>
              </div>
            </div>
          </div>
        </v-list-item>

        <v-divider></v-divider>

        <v-list-item class="task-item py-4">
          <div class="d-flex w-100">
            <v-avatar class="task-icon mr-4 primary-soft">
              <v-icon icon="mdi-brain" size="24" color="primary"></v-icon>
            </v-avatar>

            <div class="flex-grow-1">
              <div class="d-flex justify-space-between align-start mb-2">
                <div>
                  <div class="text-subtitle-1 font-weight-medium">
                    {{ t('users.privacy.dataSharing.contest2Name') }}
                  </div>
                  <div class="text-caption text-medium-emphasis mb-3">
                    {{ t('users.privacy.dataSharing.contest2Host') }}
                  </div>
                </div>
                <v-chip size="small" color="success" variant="tonal">{{
                  t('users.privacy.dataSharing.statusOngoing')
                }}</v-chip>
              </div>

              <div class="d-flex flex-wrap gap-4 mb-3">
                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colSubmittedAt') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-calendar" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest2Date') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colDataUsed') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-card-account-details" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest2Data') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colPurpose') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-information" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest2Purpose') }}</span>
                  </div>
                </div>
              </div>

              <div class="task-description text-body-2 text-medium-emphasis mb-3">
                {{ t('users.privacy.dataSharing.contest2Desc') }}
              </div>

              <div class="d-flex flex-wrap gap-2">
                <v-chip size="small" variant="flat" color="primary">{{
                  t('users.privacy.dataSharing.contest2TagLevel')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest2TagFormat')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest2TagCredit')
                }}</v-chip>
              </div>
            </div>
          </div>
        </v-list-item>

        <v-divider></v-divider>

        <v-list-item class="task-item py-4">
          <div class="d-flex w-100">
            <v-avatar class="task-icon mr-4 primary-soft">
              <v-icon icon="mdi-lightbulb" size="24" color="primary"></v-icon>
            </v-avatar>

            <div class="flex-grow-1">
              <div class="d-flex justify-space-between align-start mb-2">
                <div>
                  <div class="text-subtitle-1 font-weight-medium">
                    {{ t('users.privacy.dataSharing.contest3Name') }}
                  </div>
                  <div class="text-caption text-medium-emphasis mb-3">
                    {{ t('users.privacy.dataSharing.contest3Host') }}
                  </div>
                </div>
                <v-chip size="small" color="info" variant="tonal">{{
                  t('users.privacy.dataSharing.statusEnded')
                }}</v-chip>
              </div>

              <div class="d-flex flex-wrap gap-4 mb-3">
                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colSubmittedAt') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-calendar" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest3Date') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colDataUsed') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-card-account-details" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest3Data') }}</span>
                  </div>
                </div>

                <div class="info-item">
                  <div class="text-caption text-medium-emphasis mb-1">
                    {{ t('users.privacy.dataSharing.colPurpose') }}
                  </div>
                  <div class="d-flex align-center">
                    <v-icon icon="mdi-information" size="small" color="primary" class="mr-1"></v-icon>
                    <span class="text-body-2">{{ t('users.privacy.dataSharing.contest3Purpose') }}</span>
                  </div>
                </div>
              </div>

              <div class="task-description text-body-2 text-medium-emphasis mb-3">
                {{ t('users.privacy.dataSharing.contest3Desc') }}
              </div>

              <div class="d-flex flex-wrap gap-2">
                <v-chip size="small" variant="flat" color="primary">{{
                  t('users.privacy.dataSharing.contest3TagLevel')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest3TagFormat')
                }}</v-chip>
                <v-chip size="small" variant="flat" color="secondary">{{
                  t('users.privacy.dataSharing.contest3TagCredit')
                }}</v-chip>
              </div>
            </div>
          </div>
        </v-list-item>
      </v-list>
    </v-card>

    <!-- 使用说明 -->
    <v-alert
      type="info"
      variant="tonal"
      class="mt-4"
      border="start"
      density="comfortable"
      icon="mdi-information-outline"
    >
      <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('users.privacy.dataSharing.noticeTitle') }}</div>
      <p class="text-body-2 mb-0">
        <i18n-t keypath="users.privacy.dataSharing.noticeBody" scope="global" tag="span">
          <template #accessLog>
            <router-link :to="{ name: 'PrivacyCenterAccessLogs' }" class="text-decoration-none">
              {{ t('users.privacy.dataSharing.noticeAccessLog') }}
            </router-link>
          </template>
        </i18n-t>
      </p>
    </v-alert>
  </div>
</template>

<script lang="ts" setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// 注意：这是一个示例页面，使用了模拟数据
// 实际生产环境中应该使用后端API获取参与的实名赛题
</script>

<style scoped lang="scss">
.realname-tasks {
  .status-chip {
    font-weight: 500;
  }

  .tasks-list-card {
    background-color: var(--surface);
    border: 1px solid rgba(var(--v-border-color), 0.12);
  }

  .task-item {
    transition: background-color 0.2s ease;

    &:hover {
      background-color: rgba(var(--v-theme-on-surface), 0.01);
    }
  }

  .task-icon {
    width: 40px;
    height: 40px;
  }

  .primary-soft {
    background-color: rgba(var(--v-theme-primary), 0.08);
  }

  .info-item {
    min-width: 150px;
  }

  .gap-4 {
    gap: 1rem;
  }

  .gap-2 {
    gap: 0.5rem;
  }
}
</style>
