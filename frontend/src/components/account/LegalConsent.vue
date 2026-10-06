<template>
  <div>
    <v-checkbox v-model="agreed" density="compact" hide-details>
      <template #label>
        <span class="legal-consent__label">
          {{ t('account.iAgreeToThe') }}
          <LegalLinks />
        </span>
      </template>
    </v-checkbox>
    <p v-if="loadError" class="legal-consent__error">{{ loadError }}</p>

    <ConfirmDialog
      v-model="prompting"
      :title="t('account.consentPrompt')"
      :confirm-label="actionLabel"
      :cancel-label="t('account.cancel')"
      @confirm="settle(true)"
      @cancel="settle(false)"
    >
      <LegalLinks />
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 注册类页面的同意环节（#1486）：复选框默认不勾；不勾就提交时弹一次「请阅读并
 * 同意以下条款」，点同意等于勾上并继续提交。
 *
 * 父组件在提交时调 `confirm()`：拿到的是这次要交给后端的同意（文档版本 + 方式），
 * 拿到 null 就不提交。版本来自后端，后端会拒绝与当前版本不符的同意，所以这里
 * 不自己写死版本号——取数在 `useConsentDocuments`，由页面容器做，版本经 props 传进来。
 * 于是这一只不碰接口层，只凭 props 就画得出来。
 */
import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'

import { ref } from 'vue'

import LegalLinks from './LegalLinks.vue'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  actionLabel: string
  /** 后端当前的协议版本；null = 还没取到或没取到，`confirm()` 不放行。 */
  documents: AcceptedDocuments | null
  loadError: string
}>()

const agreed = ref(false)
const prompting = ref(false)
let resolvePrompt: ((ok: boolean) => void) | null = null

function settle(ok: boolean) {
  prompting.value = false
  resolvePrompt?.(ok)
  resolvePrompt = null
}

async function confirm(): Promise<{ documents: AcceptedDocuments; method: ConsentMethod } | null> {
  const versions = props.documents
  if (!versions) return null
  if (agreed.value) return { documents: versions, method: 'checkbox' }
  prompting.value = true
  const ok = await new Promise<boolean>((resolve) => (resolvePrompt = resolve))
  if (!ok) return null
  agreed.value = true
  return { documents: versions, method: 'dialog' }
}

defineExpose({ confirm })
</script>

<style scoped>
.legal-consent__label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.legal-consent__error {
  margin-top: 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}
</style>
