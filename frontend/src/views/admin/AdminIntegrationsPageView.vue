<script setup lang="ts">
// 「飞书应用」这一页**画的那一半**：状态行、那张表单、保存回执。
//
// 读（`getPlatformFeishuApp`）、写（`savePlatformFeishuApp`）、校验和保存状态机都在容器
// `AdminIntegrationsPage.vue` 里。这里只吃 props、只往上发事件，所以能被单独挂起来看。
import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'

defineProps<{
  loading: boolean
  /** 读这一页失败时是服务端原话（取不到就空串）；`null` 表示没失败。判据是 `!== null`。 */
  loadError: string | null
  /** 没填全（App ID 为空）—— 填写本身的问题，还没发请求。 */
  validationError: string
  saving: boolean
  saved: boolean
  saveError: string | null
  appId: string
  /** Secret 只写不回显：这一格永远是空的，空着提交表示「不改已经存下的那一个」。 */
  appSecret: string
  domain: string
  domains: { value: string; title: string }[]
  status: string
  updated: string
  /** 有答案可答（读回来了，或已经不在读）才画状态行。 */
  showStatus: boolean
  configured: boolean
}>()

defineEmits<{
  retry: []
  save: []
  'update:appId': [value: string]
  'update:appSecret': [value: string]
  'update:domain': [value: string]
}>()

const { t } = useI18n()
</script>

<template>
  <AdminPage :title="t('navigation.admin.integrations')" :sub="t('integrations.admin.sub')">
    <div class="afi__body admin-form-card">
      <!-- 读失败：标题说清是哪一页没读到，服务端原话作说明行，重试就在旁边。
           **不**接着画「还没配置」和那张表单。判据是 `!== null` 而不是真值：
           原话取不到时 `loadError` 是空串，仍要给出错态。 -->
      <BaseLoadError
        v-if="loadError !== null"
        :title="t('integrations.admin.loadFailed')"
        :error="loadError || undefined"
        :retry-label="t('integrations.admin.retry')"
        @retry="$emit('retry')"
      />

      <template v-else>
        <!-- 首屏还没读回来时不画状态行：这时候还没有答案。 -->
        <p v-if="showStatus" class="afi__status t-body" :data-configured="configured ? 'yes' : 'no'">
          {{ status }}
        </p>
        <p v-if="updated" class="afi__meta t-meta">{{ updated }}</p>

        <div class="afi__form">
          <v-text-field
            :model-value="appId"
            autocomplete="off"
            :label="t('integrations.admin.appId')"
            :disabled="loading"
            @update:model-value="$emit('update:appId', $event)"
          />
          <v-text-field
            :model-value="appSecret"
            class="afi__secret"
            autocomplete="new-password"
            type="password"
            :label="t('integrations.admin.appSecret')"
            :hint="t('integrations.admin.secretHint')"
            persistent-hint
            @update:model-value="$emit('update:appSecret', $event)"
          />
          <v-select
            :model-value="domain"
            autocomplete="off"
            :items="domains"
            :label="t('integrations.admin.domain')"
            @update:model-value="$emit('update:domain', $event)"
          />
          <p v-if="validationError" role="alert" class="afi__error t-body">{{ validationError }}</p>
          <div class="afi__actions">
            <SaveStatus
              :saving="saving"
              :saved="saved"
              :error="saveError"
              :saved-text="t('integrations.admin.saved')"
            />
            <BaseButton kind="primary" :loading="saving" :disabled="loading" @click="$emit('save')">
              {{ t('integrations.admin.save') }}
            </BaseButton>
          </div>
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 卡片的宽度（720 上限）、内边距（20/24）和内缩（16/24）都由 `.admin-form-card` 给，
   和别的表单页同一套。 */

.afi__status {
  margin: 0;
  color: var(--ink);
}

.afi__meta {
  margin: 4px 0 0;
  color: var(--faint);
}

.afi__form {
  margin-top: 16px;
}

/* Secret 那格下面挂着 hint，而下一个字段的浮动标签有一半悬在它自己框的上沿之上
   （见 `.claude/rules/frontend.md`），中间没有余量时两行字会叠在一起。给这一格
   留一段底距，标签就落在空处。 */
.afi__secret {
  margin-bottom: 12px;
}

.afi__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
}

.afi__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

/* 窄屏的两侧收窄由 `.admin-form-card` 自己那条 ≤700 规则给，这一页不再各写一份。 */
</style>
