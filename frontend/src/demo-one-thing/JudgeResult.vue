<script setup lang="ts">
// One judged submission: the score and its one-line summary, then a row per
// test (✓ / ✗, the time it took, and what went wrong). Shown under the editor
// right after 提交评测 and again when a row of 评测记录 is opened.
import type { Submission } from './course'

defineProps<{ sub: Submission }>()
</script>

<template>
  <div class="ot-judge">
    <div class="ot-judge__verdict" :class="{ 'is-ok': sub.score === sub.total }">
      <v-icon size="16" :icon="sub.score === sub.total ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'" />
      <b>{{ sub.score === sub.total ? '已通过' : '没有全部通过' }}</b>
      <span>{{ sub.summary }}</span>
    </div>
    <div v-for="r in sub.rows" :key="r.name" class="ot-judge__row" :class="{ 'is-bad': !r.ok }">
      <span class="ot-judge__mark">{{ r.ok ? '✓' : '✗' }}</span>
      <span class="ot-judge__text"
        ><span class="ot-judge__name">{{ r.name }}</span
        ><span v-if="r.note" class="ot-judge__note">{{ r.note }}</span></span
      >
      <span class="ot-judge__ms">{{ r.ms }} ms</span>
    </div>
  </div>
</template>

<style scoped>
.ot-judge {
  font-size: 12px;
  line-height: 1.55;
}

.ot-judge__verdict {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 4px;
  font-size: 13px;
  color: var(--text);
}

.ot-judge__verdict b {
  color: var(--ink);
}

.ot-judge__verdict .v-icon {
  color: var(--muted);
}

.ot-judge__verdict.is-ok .v-icon {
  color: var(--ok-ink);
}

.ot-judge__row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-family: var(--font-mono);
  color: var(--text);
}

.ot-judge__mark {
  flex: none;
  width: 12px;
  color: var(--ok);
}

.ot-judge__row.is-bad .ot-judge__mark {
  color: var(--danger-ink);
}

.ot-judge__text {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 0 8px;
  min-width: 0;
}

.ot-judge__name {
  overflow-wrap: anywhere;
}

.ot-judge__note {
  font-family: var(--font-sans);
  color: var(--muted);
}

.ot-judge__ms {
  flex: none;
  color: var(--faint);
}
</style>
