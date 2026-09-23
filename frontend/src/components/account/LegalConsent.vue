<template>
  <div>
    <v-checkbox v-model="agreed" density="compact" hide-details>
      <template #label>
        <span class="text-body-2" style="color: var(--muted); line-height: 1.4">
          {{ t('account.iAgreeToThe') }}
          <LegalLinks />
        </span>
      </template>
    </v-checkbox>
    <p v-if="loadError" class="text-body-2 mt-1" style="color: rgb(var(--v-theme-error))">{{ loadError }}</p>

    <v-dialog v-model="prompting" max-width="420" persistent>
      <v-card>
        <v-card-title class="text-h6">{{ t('account.consentPrompt') }}</v-card-title>
        <v-card-text class="text-body-2">
          <LegalLinks />
        </v-card-text>
        <v-card-actions class="justify-end pa-4">
          <v-btn variant="text" @click="settle(false)">{{ t('account.cancel') }}</v-btn>
          <v-btn color="primary" variant="flat" @click="settle(true)">{{ actionLabel }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 注册类页面的同意环节（#1486）：复选框默认不勾；不勾就提交时弹一次「请阅读并
 * 同意以下条款」，点同意等于勾上并继续提交。
 *
 * 父组件在提交时调 `confirm()`：拿到的是这次要交给后端的同意（文档版本 + 方式），
 * 拿到 null 就不提交。版本来自后端，后端会拒绝与当前版本不符的同意，所以这里
 * 不自己写死版本号。
 */
import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'

import { onMounted, ref } from 'vue'

import LegalLinks from './LegalLinks.vue'

import { t } from '@/i18n'
import { LegalApi } from '@/network/api/legal'

defineProps<{ actionLabel: string }>()

const agreed = ref(false)
const prompting = ref(false)
const loadError = ref('')
let documents: AcceptedDocuments | null = null
let resolvePrompt: ((ok: boolean) => void) | null = null

async function loadVersions(): Promise<AcceptedDocuments | null> {
  if (documents) return documents
  try {
    const { data } = await LegalApi.listDocuments()
    documents = Object.fromEntries(data.documents.map((d) => [d.document, d.version]))
    loadError.value = ''
  } catch {
    loadError.value = t('account.legalDocumentsUnavailable')
  }
  return documents
}

onMounted(loadVersions)

function settle(ok: boolean) {
  prompting.value = false
  resolvePrompt?.(ok)
  resolvePrompt = null
}

async function confirm(): Promise<{ documents: AcceptedDocuments; method: ConsentMethod } | null> {
  const versions = await loadVersions()
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
