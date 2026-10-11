<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'

// 管理后台的「飞书应用」（`/admin/integrations`）的画法：状态行、三格凭据、保存回执。
// 取数与写都在页面里（`AdminIntegrationsPage.vue`），这里只吃数据、往上报动作。
//
// 三件事决定了这一块的形状（判据与来由见页面文件开头）：
// 1. Secret 只写不回显 —— 输入框绑一支模型，永远从空开始；空着提交表示「不改」。
// 2. 有没有配过是页面上第一个问题，状态行由页面算好传进来；读失败时整块换成「读不到」，
//    这时候我们并没有那个答案，所以不画状态行也不画表单。
// 3. 保存失败说服务端原话，表单里的东西一个字不动。
defineOptions({ name: 'AdminIntegrationsPageView' })

const { t } = useI18n()

defineProps<{
  loading: boolean
  /** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。 */
  loadError: string | null
  /** 读回来之后才敢画的那行答案：有没有配过、配的哪一家。 */
  status: string
  /** 首屏还没读回来时不画状态行：这时候还没有答案。 */
  showStatus: boolean
  /** 状态行那个 `data-configured`：有没有配过是给成员看的那个答案。 */
  configured: boolean
  updated: string
  saving: boolean
  saved: boolean
  /** 保存失败。就地回执，表单里的东西一个字不动。 */
  saveError: string
  /** 表单没填全（App ID 为空）—— 这是填写本身的问题，还没发请求。 */
  validationError: string
}>()

const emit = defineEmits<{
  retry: []
  save: []
}>()

const appId = defineModel<string>('appId', { required: true })
const appSecret = defineModel<string>('appSecret', { required: true })
const domain = defineModel<string>('domain', { required: true })

const domains = computed(() => [
  { value: 'feishu', title: t('admin.integrations.domainFeishu') },
  { value: 'lark', title: 'Lark（larksuite.com）' },
])
</script>

<template>
  <AdminPage :title="t('navigation.admin.integrations')" :sub="t('integrations.admin.sub')">
    <div class="afi__body admin-form-card">
      <!-- 读失败：标题说清是哪一页没读到，服务端原话作说明行，重试就在旁边。
           **不**接着画「还没配置」和那张表单 —— 见文件开头第 4 条。
           判据是 `!== null` 而不是真值：原话取不到时 `loadError` 是空串，仍要给出错态。 -->
      <BaseLoadError
        v-if="loadError !== null"
        :title="t('integrations.admin.loadFailed')"
        :error="loadError || undefined"
        :retry-label="t('integrations.admin.retry')"
        @retry="emit('retry')"
      />

      <template v-else>
        <p v-if="showStatus" class="afi__status t-body" :data-configured="configured ? 'yes' : 'no'">
          {{ status }}
        </p>
        <p v-if="updated" class="afi__meta t-meta">{{ updated }}</p>

        <div class="afi__form">
          <v-text-field v-model="appId" autocomplete="off" :label="t('integrations.admin.appId')" :disabled="loading" />
          <v-text-field
            v-model="appSecret"
            class="afi__secret"
            autocomplete="new-password"
            type="password"
            :label="t('integrations.admin.appSecret')"
            :hint="t('integrations.admin.secretHint')"
            persistent-hint
          />
          <v-select v-model="domain" autocomplete="off" :items="domains" :label="t('integrations.admin.domain')" />
          <p v-if="validationError" role="alert" class="afi__error t-body">{{ validationError }}</p>
          <div class="afi__actions">
            <SaveStatus
              :saving="saving"
              :saved="saved"
              :error="saveError"
              :saved-text="t('integrations.admin.saved')"
            />
            <BaseButton kind="primary" :loading="saving" :disabled="loading" @click="emit('save')">
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
