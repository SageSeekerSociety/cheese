<script setup lang="ts">
// 存的时候和 AI 队友这段时间的修改重叠了：重叠的每一处三栏并排（打开时、你的、它的），
// 逐处选一个版本，选完再存。不重叠的地方已经合好了，不用再看。
import type { MergeRegion } from '@/types/reviewComment'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(defineProps<{ regions: MergeRegion[]; agentName: string; busy?: boolean }>(), {
  busy: false,
})

const emit = defineEmits<{
  (e: 'resolve', content: string): void
  (e: 'cancel'): void
}>()

type Side = 'mine' | 'theirs'
// 每一处默认用你的：你刚写下的那一份最可能是你要的，要换成它的再点。
const picks = ref<Side[]>([])
watch(
  () => props.regions,
  (regions) => {
    picks.value = regions.filter((r) => r.kind === 'conflict').map(() => 'mine')
  },
  { immediate: true }
)

const conflicts = computed(() =>
  props.regions.flatMap((r, i) => (r.kind === 'conflict' ? [{ region: r, index: i }] : []))
)

function resolve() {
  let n = 0
  const text = props.regions
    .map((r) => {
      if (r.kind === 'same') return r.text
      const side = picks.value[n++]
      return side === 'theirs' ? r.theirs : r.mine
    })
    .join('')
  emit('resolve', text)
}
</script>

<template>
  <div class="merge-picker">
    <div class="merge-picker__banner">
      <v-icon size="16" class="merge-picker__icon">mdi-alert-circle-outline</v-icon>
      <span>{{ t('work.room.review.mergeBanner', { agent: agentName, count: conflicts.length }) }}</span>
    </div>
    <div v-for="(c, n) in conflicts" :key="c.index" class="merge-picker__three">
      <div class="merge-picker__col">
        <div class="merge-picker__head">{{ t('work.room.review.mergeBase') }}</div>
        <pre class="merge-picker__code">{{ c.region.base || ' ' }}</pre>
      </div>
      <button
        type="button"
        class="merge-picker__col merge-picker__col--pick"
        :class="{ 'merge-picker__col--on': picks[n] === 'mine' }"
        :aria-pressed="picks[n] === 'mine'"
        @click="picks[n] = 'mine'"
      >
        <span class="merge-picker__head">
          {{ t('work.room.review.mergeMine') }}
          <v-icon v-if="picks[n] === 'mine'" size="14">mdi-check</v-icon>
        </span>
        <pre class="merge-picker__code">{{ c.region.mine || ' ' }}</pre>
      </button>
      <button
        type="button"
        class="merge-picker__col merge-picker__col--pick"
        :class="{ 'merge-picker__col--on': picks[n] === 'theirs' }"
        :aria-pressed="picks[n] === 'theirs'"
        @click="picks[n] = 'theirs'"
      >
        <span class="merge-picker__head">
          {{ t('work.room.review.mergeTheirs', { agent: agentName }) }}
          <v-icon v-if="picks[n] === 'theirs'" size="14">mdi-check</v-icon>
        </span>
        <pre class="merge-picker__code">{{ c.region.theirs || ' ' }}</pre>
      </button>
    </div>
    <div class="merge-picker__actions">
      <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('cancel')">
        {{ t('work.room.accept.cancel') }}
      </BaseButton>
      <BaseButton kind="primary" size="sm" :loading="busy" :disabled="busy" @click="resolve">
        {{ t('work.room.review.mergeSave') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.merge-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}
.merge-picker__banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--warn-wash);
  color: var(--warn-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.merge-picker__icon {
  flex: none;
  margin-top: 2px;
}
.merge-picker__three {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
.merge-picker__col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  text-align: left;
}
.merge-picker__col--pick {
  cursor: pointer;
}
.merge-picker__col--pick:hover {
  background: var(--fill);
}
.merge-picker__col--on {
  border-color: var(--ink);
}
.merge-picker__head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.merge-picker__code {
  margin: 0;
  padding: 4px 8px;
  overflow-x: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.merge-picker__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
