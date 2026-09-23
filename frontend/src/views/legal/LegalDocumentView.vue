<template>
  <main class="legal">
    <article class="legal-column">
      <p v-if="error" class="t-reading">{{ error }}</p>
      <template v-else-if="doc">
        <p class="t-meta-read legal-meta">
          {{ t('account.legalVersion', { version: doc.version }) }} ·
          {{ t('account.legalEffectiveDate', { date: doc.effectiveDate }) }}
        </p>
        <!-- 正文来自后端（backend/app/domain/legal/texts），和同意记录里的哈希是同一份 -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="legal-body t-reading" v-html="html" />
      </template>
    </article>
  </main>
</template>

<script setup lang="ts">
/**
 * 用户协议 / 隐私政策的公开页（#1486）。不登录也能看，注册页、登录页和重新
 * 同意的弹窗都新开一页链到这里。`?version=` 可以看任何一个发布过的版本。
 */
import type { LegalDocumentFull, LegalDocumentKey } from '@/network/api/legal/types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { t } from '@/i18n'
import { markdown, sanitizeRendered } from '@/lib/markdown'
import { LegalApi } from '@/network/api/legal'

const props = defineProps<{ document: LegalDocumentKey }>()
const route = useRoute()

const doc = ref<LegalDocumentFull | null>(null)
const error = ref('')

const html = computed(() => (doc.value ? sanitizeRendered(markdown.parse(doc.value.content) as string) : ''))

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

<style scoped>
.legal {
  min-height: 100vh;
  background: var(--canvas);
  color: var(--text);
  padding: 32px 16px;
}

.legal-column {
  max-width: var(--page-w-read);
  margin: 0 auto;
}

.legal-meta {
  margin-bottom: 8px;
}

.legal-body :deep(h1) {
  color: var(--ink);
  font-size: 23px;
  line-height: var(--lh-23);
  margin-bottom: 24px;
}

.legal-body :deep(h2) {
  color: var(--ink);
  font-size: 18px;
  line-height: var(--lh-18);
  margin: 32px 0 12px;
}

.legal-body :deep(h3) {
  color: var(--ink);
  font-size: 15px;
  line-height: var(--lh-15);
  margin: 24px 0 8px;
}

.legal-body :deep(p),
.legal-body :deep(ul),
.legal-body :deep(ol) {
  margin-bottom: 12px;
}

.legal-body :deep(ul),
.legal-body :deep(ol) {
  padding-left: 24px;
}

.legal-body :deep(strong) {
  color: var(--ink);
}

.legal-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 16px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.legal-body :deep(th),
.legal-body :deep(td) {
  border: 1px solid var(--line);
  padding: 8px;
  text-align: left;
  vertical-align: top;
}

.legal-body :deep(th) {
  background: var(--fill);
}
</style>
