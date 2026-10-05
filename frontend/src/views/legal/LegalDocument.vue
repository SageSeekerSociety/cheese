<!--
  The public page for the terms of service and the privacy policy (#1486). It
  reads which document the route names and the `?version=` it asks for, fetches
  that version, and what it shows is LegalDocumentView.vue.
-->
<template>
  <LegalDocumentView :doc="doc" :error="error" />
</template>

<script setup lang="ts">
/**
 * 用户协议 / 隐私政策的公开页（#1486）。不登录也能看，注册页、登录页和重新
 * 同意的弹窗都新开一页链到这里。`?version=` 可以看任何一个发布过的版本。
 * 取数留在这里，画面在 `LegalDocumentView.vue`。
 */
import type { LegalDocumentFull, LegalDocumentKey } from '@/network/api/legal/types'

import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import LegalDocumentView from './LegalDocumentView.vue'

import { t } from '@/i18n'
import { LegalApi } from '@/network/api/legal'

const props = defineProps<{ document: LegalDocumentKey }>()
const route = useRoute()

const doc = ref<LegalDocumentFull | null>(null)
const error = ref('')

watch(
  () => [props.document, route.query.version] as const,
  async ([document, version]) => {
    error.value = ''
    try {
      const { data } = await LegalApi.getDocument(document, typeof version === 'string' ? version : undefined)
      doc.value = data
    } catch {
      doc.value = null
      error.value = t('account.legalLoadFailed')
    }
  },
  { immediate: true }
)
</script>
