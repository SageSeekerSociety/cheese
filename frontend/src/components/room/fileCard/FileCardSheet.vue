<script setup lang="ts">
// 表格卡片的正文：第一张表左上角 5×5，和「共几行」。这条消息之后表又被改过时，把这条
// 消息那一版和现在这一版逐格比，改过的格子在这一块里标出来，总数交给卡片头上那颗
// 「改了 N 格」。比的是两份真字节（房间文件的历史版本），不估不猜。
import type { SheetPeek } from '@/lib/sheetCells'

import { inject, ref, watch } from 'vue'

import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'
import { FILE_FACTS_SOURCE, NO_FILE_FACTS } from '@/lib/fileFactsSource'
import { changedCells, peekSheet } from '@/lib/sheetCells'

const props = defineProps<{
  topicId: string | null
  path: string
  /** 卡片进了视野没有：没进就不下字节。 */
  seen: boolean
  /** 这条消息发的那一版（之后又改过时才给）：拿它和现在这份比。 */
  baseVersionId: string | null
}>()
const emit = defineEmits<{ changes: [count: number | null]; sheets: [names: string[]] }>()

const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const facts = inject(FILE_FACTS_SOURCE, NO_FILE_FACTS)

const peek = ref<SheetPeek | null>(null)
const changed = ref<Set<string>>(new Set())
const failed = ref(false)
let generation = 0

async function load() {
  const mine = ++generation
  if (!props.seen || !props.topicId || !props.path) return
  failed.value = false
  try {
    const bytes = await source.fileBytes(props.topicId, props.path)
    if (mine !== generation) return
    peek.value = await peekSheet(bytes)
    if (mine !== generation) return
    emit('sheets', peek.value.sheetNames)
    if (!props.baseVersionId) {
      changed.value = new Set()
      emit('changes', null)
      return
    }
    const before = await facts.versionBytes(props.topicId, props.baseVersionId)
    if (mine !== generation) return
    changed.value = await changedCells(before, bytes)
    if (mine === generation) emit('changes', changed.value.size)
  } catch {
    if (mine === generation) failed.value = true
  }
}

watch(() => [props.seen, props.topicId, props.path, props.baseVersionId], load, { immediate: true })

const isChanged = (address: string) => !!peek.value && changed.value.has(`${peek.value.firstSheet}!${address}`)
</script>

<template>
  <div class="fc-sheet">
    <table v-if="peek && peek.rows.length" class="fc-sheet__table">
      <tbody>
        <tr v-for="(row, r) in peek.rows" :key="r">
          <component
            :is="r === 0 ? 'th' : 'td'"
            v-for="(cell, c) in row"
            :key="c"
            :class="{ 'fc-sheet__cell--changed': isChanged(peek.addresses[r][c]) }"
            :title="isChanged(peek.addresses[r][c]) ? t('work.room.fileCard.cellChanged') : undefined"
            >{{ cell }}</component
          >
        </tr>
      </tbody>
    </table>
    <span v-else-if="failed" class="fc-sheet__note">{{ t('work.room.fileCard.sheetFailed') }}</span>
    <span v-else class="fc-sheet__placeholder" aria-hidden="true" />
    <span v-if="peek && peek.rowCount" class="fc-sheet__note">
      {{ t('work.room.fileCard.sheetRows', { n: peek.rowCount }) }}
    </span>
  </div>
</template>

<style scoped>
.fc-sheet {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.fc-sheet__table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.fc-sheet__table th,
.fc-sheet__table td {
  padding: 5px 8px;
  overflow: hidden;
  text-align: left;
  white-space: nowrap;
  text-overflow: ellipsis;
  border: 1px solid var(--line);
}
.fc-sheet__table th {
  font-weight: 400;
  color: var(--muted);
  background: var(--fill);
}
/* 这条消息之后改过的格子：琥珀浅底配正文色，和卡片头上「改了 N 格」是同一件事。 */
.fc-sheet__table .fc-sheet__cell--changed {
  color: var(--accent-ink);
  background: var(--accent-wash);
}
.fc-sheet__placeholder {
  height: 120px;
  border-radius: var(--radius-sm);
  background: var(--fill);
}
.fc-sheet__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
