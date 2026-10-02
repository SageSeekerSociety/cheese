<script setup lang="ts">
import type { DocAiDisplayContext } from '../../../lib/docAiTypes'

import { t } from '@/i18n'

defineProps<{ context?: DocAiDisplayContext; compact?: boolean; hideQuestion?: boolean; collapsed?: boolean }>()
</script>

<template>
  <section class="doc-ai-context" :class="{ 'is-compact': compact }">
    <details v-if="!hideQuestion && context?.question !== undefined" open>
      <summary>{{ t('work.room.docAi.contextQuestion') }}</summary>
      <pre dir="auto">{{ context.question }}</pre>
    </details>
    <details v-if="context?.state === 'verified'" :open="!compact && !collapsed && context.scope === 'selection'">
      <summary>
        {{ t(context.scope === 'selection' ? 'work.room.docAi.contextSelection' : 'work.room.docAi.contextDocument') }}
        <span v-if="compact" class="doc-ai-context__excerpt" dir="auto">{{ context.original }}</span>
      </summary>
      <pre dir="auto">{{ context.original }}</pre>
    </details>
    <p v-else role="status">
      {{ t(context?.state === 'invalid' ? 'work.room.docAi.contextInvalid' : 'work.room.docAi.contextUnavailable') }}
    </p>
  </section>
</template>

<style scoped>
.doc-ai-context {
  min-width: 0;
  font-size: 13px;
}

details {
  margin-block: 6px;
}

summary {
  color: var(--muted);
  cursor: pointer;
}

summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

pre {
  max-height: 160px;
  overflow: auto;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin-block: 8px;
}

p {
  color: var(--muted);
}
.is-compact {
  margin-bottom: 12px;
}
.is-compact summary {
  list-style: none;
  border-inline-start: 2px solid var(--line-2);
  padding-inline-start: 8px;
}
.doc-ai-context__excerpt {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  color: var(--text);
  margin-top: 4px;
  line-height: var(--lh-13);
}
details[open] .doc-ai-context__excerpt {
  display: none;
}
pre {
  font-family: var(--font-sans);
}
</style>
