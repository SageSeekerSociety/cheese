<template>
  <div class="privacy-policy">
    <div class="d-flex justify-space-between align-center mb-6">
      <h2 class="text-h5 font-weight-medium mb-0">{{ t('users.privacy.policy.title') }}</h2>
      <v-chip color="primary" variant="tonal" size="small" class="status-chip">
        <template #prepend>
          <v-icon icon="mdi-shield-lock" size="small"></v-icon>
        </template>
        {{ t('users.privacy.policy.updatedAt', { date: '2023/12/01' }) }}
      </v-chip>
    </div>

    <p class="text-body-2 text-medium-emphasis mb-6">
      {{ t('users.privacy.policy.intro') }}
    </p>

    <!-- 政策导航卡片 -->
    <v-card class="mb-8 navigation-card" variant="flat" rounded="lg">
      <v-card-text>
        <div class="d-flex align-center mb-4">
          <v-avatar rounded size="42" class="primary-soft mr-3">
            <v-icon icon="mdi-text-box-search" size="24" color="primary"></v-icon>
          </v-avatar>
          <div class="text-subtitle-1 font-weight-medium">{{ t('users.privacy.policy.quickNav') }}</div>
        </div>

        <v-row class="policy-nav">
          <v-col cols="12" sm="6" md="3">
            <v-btn
              variant="flat"
              color="primary"
              block
              class="nav-btn justify-start"
              @click="scrollToSection('information-collection')"
            >
              <v-icon icon="mdi-file-document-outline" class="mr-2"></v-icon>
              {{ t('users.privacy.policy.navCollection') }}
            </v-btn>
          </v-col>

          <v-col cols="12" sm="6" md="3">
            <v-btn
              variant="flat"
              color="primary"
              block
              class="nav-btn justify-start"
              @click="scrollToSection('information-usage')"
            >
              <v-icon icon="mdi-clipboard-text-outline" class="mr-2"></v-icon>
              {{ t('users.privacy.policy.navUsage') }}
            </v-btn>
          </v-col>

          <v-col cols="12" sm="6" md="3">
            <v-btn
              variant="flat"
              color="primary"
              block
              class="nav-btn justify-start"
              @click="scrollToSection('information-protection')"
            >
              <v-icon icon="mdi-shield-check" class="mr-2"></v-icon>
              {{ t('users.privacy.policy.navProtection') }}
            </v-btn>
          </v-col>

          <v-col cols="12" sm="6" md="3">
            <v-btn
              variant="flat"
              color="primary"
              block
              class="nav-btn justify-start"
              @click="scrollToSection('user-rights')"
            >
              <v-icon icon="mdi-account-check" class="mr-2"></v-icon>
              {{ t('users.privacy.policy.navRights') }}
            </v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 政策内容 -->
    <v-card class="mb-6 policy-card" variant="flat" rounded="lg">
      <v-card-text>
        <!-- 信息收集 -->
        <div id="information-collection" class="policy-section mb-8">
          <div class="section-header d-flex align-start mb-4">
            <v-avatar rounded size="42" class="primary-soft mr-3">
              <v-icon icon="mdi-file-document-outline" size="24" color="primary"></v-icon>
            </v-avatar>
            <div>
              <h3 class="text-h6 font-weight-medium mb-1">{{ t('users.privacy.policy.collectionTitle') }}</h3>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('users.privacy.policy.collectionSubtitle') }}</p>
            </div>
          </div>

          <div class="section-content">
            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.collectionAccountTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.collectionAccountBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.collectionRealNameTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.collectionRealNameBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.collectionUsageTitle') }}
            </h4>
            <p class="text-body-2 mb-2">{{ t('users.privacy.policy.collectionUsageBody') }}</p>
            <ul class="text-body-2 mb-4">
              <li>{{ t('users.privacy.policy.collectionUsageBullet1') }}</li>
              <li>{{ t('users.privacy.policy.collectionUsageBullet2') }}</li>
              <li>{{ t('users.privacy.policy.collectionUsageBullet3') }}</li>
              <li>{{ t('users.privacy.policy.collectionUsageBullet4') }}</li>
            </ul>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.collectionCookiesTitle') }}
            </h4>
            <p class="text-body-2 mb-0">
              {{ t('users.privacy.policy.collectionCookiesBody') }}
            </p>
          </div>
        </div>

        <!-- 信息使用 -->
        <v-divider class="my-4"></v-divider>

        <div id="information-usage" class="policy-section mb-8">
          <div class="section-header d-flex align-start mb-4">
            <v-avatar rounded size="42" class="primary-soft mr-3">
              <v-icon icon="mdi-clipboard-text-outline" size="24" color="primary"></v-icon>
            </v-avatar>
            <div>
              <h3 class="text-h6 font-weight-medium mb-1">{{ t('users.privacy.policy.usageTitle') }}</h3>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('users.privacy.policy.usageSubtitle') }}</p>
            </div>
          </div>

          <div class="section-content">
            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.usageServiceTitle') }}</h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.usageServiceBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.usageContestTitle') }}</h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.usageContestBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.usageSecurityTitle') }}</h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.usageSecurityBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.usageAnalyticsTitle') }}</h4>
            <p class="text-body-2 mb-0">
              {{ t('users.privacy.policy.usageAnalyticsBody') }}
            </p>
          </div>
        </div>

        <!-- 信息保护 -->
        <v-divider class="my-4"></v-divider>

        <div id="information-protection" class="policy-section mb-8">
          <div class="section-header d-flex align-start mb-4">
            <v-avatar rounded size="42" class="primary-soft mr-3">
              <v-icon icon="mdi-shield-check" size="24" color="primary"></v-icon>
            </v-avatar>
            <div>
              <h3 class="text-h6 font-weight-medium mb-1">{{ t('users.privacy.policy.protectionTitle') }}</h3>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('users.privacy.policy.protectionSubtitle') }}</p>
            </div>
          </div>

          <div class="section-content">
            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.protectionEncryptionTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.protectionEncryptionBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.protectionAccessTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.protectionAccessBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.protectionAuditTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.protectionAuditBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.protectionIsolationTitle') }}
            </h4>
            <p class="text-body-2 mb-0">
              {{ t('users.privacy.policy.protectionIsolationBody') }}
            </p>
          </div>
        </div>

        <!-- 用户权利 -->
        <v-divider class="my-4"></v-divider>

        <div id="user-rights" class="policy-section">
          <div class="section-header d-flex align-start mb-4">
            <v-avatar rounded size="42" class="primary-soft mr-3">
              <v-icon icon="mdi-account-check" size="24" color="primary"></v-icon>
            </v-avatar>
            <div>
              <h3 class="text-h6 font-weight-medium mb-1">{{ t('users.privacy.policy.rightsTitle') }}</h3>
              <p class="text-body-2 text-medium-emphasis mb-0">{{ t('users.privacy.policy.rightsSubtitle') }}</p>
            </div>
          </div>

          <div class="section-content">
            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.rightsAccessTitle') }}</h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.rightsAccessBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.rightsAccessLogTitle') }}
            </h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.rightsAccessLogBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">{{ t('users.privacy.policy.rightsDeletionTitle') }}</h4>
            <p class="text-body-2 mb-4">
              {{ t('users.privacy.policy.rightsDeletionBody') }}
            </p>

            <h4 class="text-subtitle-2 font-weight-medium mb-2">
              {{ t('users.privacy.policy.rightsPermissionsTitle') }}
            </h4>
            <p class="text-body-2 mb-0">
              {{ t('users.privacy.policy.rightsPermissionsBody') }}
            </p>
          </div>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>

<script lang="ts" setup>
import { useI18n } from 'vue-i18n'
import { useGoTo } from 'vuetify'

const goTo = useGoTo()
const { t } = useI18n()

// 修改滚动函数
const scrollToSection = (sectionId: string) => {
  const element = document.getElementById(sectionId)
  if (element) {
    const offset = getComputedStyle(element).getPropertyValue('--v-layout-top').replace('px', '').trim()
    console.log(offset)
    goTo(`#${sectionId}`, { offset: -parseInt(offset) })
  }
}
</script>

<style scoped lang="scss">
.privacy-policy {
  // 添加滚动行为
  scroll-behavior: smooth;

  .status-chip {
    font-weight: 500;
  }

  .navigation-card {
    background: linear-gradient(135deg, rgba(var(--v-theme-primary), 0.02), rgba(var(--v-theme-primary), 0.04));

    .nav-btn {
      text-transform: none;
      letter-spacing: normal;
      height: 48px;
      opacity: 0.9;

      &:hover {
        opacity: 1;
      }
    }
  }

  .policy-card {
    background-color: var(--surface);
    border: 1px solid rgba(var(--v-border-color), 0.12);

    .policy-section {
      scroll-margin-top: 80px;
    }

    .section-content {
      padding-left: 56px;

      ul {
        padding-left: 18px;
      }
    }
  }

  .primary-soft {
    background-color: rgba(var(--v-theme-primary), 0.08);
  }
}
</style>
