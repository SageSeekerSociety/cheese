<script setup lang="ts">
import type { DocAiDisplayContext } from '../../../lib/docAiTypes'

import { t } from '@/i18n'

defineProps<{ context?: DocAiDisplayContext }>()
</script>

<template>
  <section class="doc-ai-context">
    <details v-if="context?.question !== undefined" open>
      <summary>{{ t('work.room.docAi.contextQuestion') }}</summary>
      <pre dir="auto">{{ context.question }}</pre>
    </details>
    <details v-if="context?.state === 'verified'" :open="context.scope === 'selection'">
      <summary>
        {{ t(context.scope === 'selection' ? 'work.room.docAi.contextSelection' : 'work.room.docAi.contextDocument') }}
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
</style>
