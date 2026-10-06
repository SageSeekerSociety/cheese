<template>
  <!-- Agree / disagree gate: title, the documents that changed, and the two choices. -->
  <ConfirmDialog
    :model-value="pending.length > 0"
    :title="t('account.rulesUpdated')"
    :confirm-label="t('account.agreeAndContinue')"
    :cancel-label="t('account.disagreeAndSignOut')"
    :loading="accepting"
    @confirm="emit('accept')"
    @cancel="emit('decline')"
  >
    <p class="mb-3">{{ t('account.rulesUpdatedBody') }}</p>
    <ul class="pl-4">
      <li v-for="doc in pending" :key="doc.document">
        <NavLink
          :to="{ name: doc.document === 'terms' ? 'LegalTerms' : 'LegalPrivacy' }"
          target="_blank"
          class="text-primary text-decoration-none"
        >
          {{ doc.title }}
        </NavLink>
        <span style="color: var(--muted)"> · {{ t('account.legalEffectiveDate', { date: doc.effectiveDate }) }} </span>
      </li>
    </ul>
    <p v-if="error" class="mt-3" style="color: rgb(var(--v-theme-error))">{{ error }}</p>
  </ConfirmDialog>
</template>

<script setup lang="ts">
/**
 * 协议实质变更后的重新同意（#1486）：画那份待同意的清单，把两个选择交出去。
 *
 * 取数、同意、退出登录都在 `usePendingConsent`（应用外壳 App.vue 调它）——
 * 组件拿 props 画、把意图 emit 出去（.claude/rules/architecture.md 第一条）。
 */
import type { LegalDocumentSummary } from '@/network/api/legal/types'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

defineProps<{
  /** 待同意的几份协议；空数组就是没事，弹窗不出现。 */
  pending: LegalDocumentSummary[]
  /** 同意这件事正在路上。 */
  accepting: boolean
  /** 同意失败的原因。 */
  error: string
}>()

const emit = defineEmits<{ accept: []; decline: [] }>()
</script>
