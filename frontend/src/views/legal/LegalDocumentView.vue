<!--
  What one published terms-of-service or privacy-policy version looks like
  (LegalDocument.vue): its version and effective date, then its own markdown. It
  is drawn from the document the container hands it and fetches nothing itself.
-->
<template>
  <main class="legal">
    <article class="legal-column">
      <p v-if="error" class="t-reading">{{ error }}</p>
      <template v-else-if="doc">
        <p class="t-meta-read legal-meta">
          {{ t('account.legalVersion', { version: doc.version }) }} ·
          {{ t('account.legalEffectiveDate', { date: doc.effectiveDate }) }}
        </p>
        <!-- The body comes from the backend (backend/app/domain/legal/texts): the same
             text the consent record hashes. -->
        <MarkdownView class="legal-body t-reading" :source="doc.content" />
      </template>
    </article>
  </main>
</template>

<script setup lang="ts">
import type { LegalDocumentFull } from '@/network/api/legal/types'

import MarkdownView from '@/components/common/MarkdownView.vue'
import { t } from '@/i18n'

defineProps<{
  /** The version to draw; null while it is being read or when it could not be. */
  doc: LegalDocumentFull | null
  /** The message shown in its place when the version could not be read. */
  error: string
}>()
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
<style>
/* The workspace locks document scrolling (styles/common.scss); a legal
   document is read as a page, so it scrolls as one, like the landing page. */
html:has(.legal) {
  overflow-y: auto !important;
}

body:has(.legal),
#app:has(.legal) {
  height: auto;
  overflow: visible;
}
</style>
