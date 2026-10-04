<script setup lang="ts">
// 修改记录：这篇文档的每一版，新的在上。点一版看它当时的全文，能改时可以恢复到那一版
// （恢复是在最新一版上再记一版，原来的几版都还在）。
//
// 一个人连着打字，几秒就存一版；同一个人相隔不到十分钟连着存的几版在列表里算一条，
// 看的、恢复的都是其中最后那一版。
import type { DocVersion, DocVersionPage } from '../../../lib/docHistory'

import { computed, ref, watch } from 'vue'

import { relTime } from '../../../lib/relTime'
import MarkdownView from '../../common/MarkdownView.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

const props = defineProps<{
  open: boolean
  /** 一页版本，新的在前；`before` 是上一页的 cursor。 */
  load: (before?: number) => Promise<DocVersionPage>
  /** 把 `version` 恢复成正文，记在 `expected`（最新一版）之上。 */
  restore?: (version: number, expected: number) => Promise<unknown>
  editable: boolean
  /** handle → 读得懂的名字。 */
  nameOf: (handle: string) => string
  mentionNames: Record<string, string>
}>()
const emit = defineEmits<{
  (e: 'update:open', open: boolean): void
  (e: 'restored'): void
}>()

const versions = ref<DocVersion[]>([])
const cursor = ref<number | null>(null)
const selected = ref<number | null>(null)
const loading = ref(false)
const restoring = ref(false)
const error = ref<string | null>(null)
// 每开一次换一个号：关掉之后才回来的那一页不认。
let session = 0

async function more(first = false) {
  const id = session
  loading.value = true
  error.value = null
  try {
    const page = await props.load(first ? undefined : cursor.value ?? undefined)
    if (id !== session) return
    versions.value = first ? page.versions : [...versions.value, ...page.versions]
    cursor.value = page.versions.length ? page.cursor : null
    if (first) selected.value = page.versions[0]?.version ?? null
  } catch (cause) {
    if (id === session)
      error.value = cause instanceof Error && cause.message ? cause.message : t('work.room.doc.historyFailed')
  } finally {
    if (id === session) loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    session++
    versions.value = []
    cursor.value = null
    selected.value = null
    error.value = null
    restoring.value = false
    if (open) void more(true)
  },
  { immediate: true }
)

/** 同一个人（同一个人让 AI 队友改）相隔不到这么久连着存的几版，算一条。 */
const RUN_MS = 10 * 60_000
const entries = computed(() => {
  const out: DocVersion[] = []
  let newer: DocVersion | null = null
  for (const v of versions.value) {
    const joins =
      newer &&
      newer.actor === v.actor &&
      newer.requested_by === v.requested_by &&
      Date.parse(newer.created_at) - Date.parse(v.created_at) < RUN_MS
    if (!joins) out.push(v)
    newer = v
  }
  return out
})

const current = computed(() => versions.value.find((v) => v.version === selected.value) ?? null)
const latest = computed(() => versions.value[0] ?? null)
const names = computed(() => ({ mentionNames: props.mentionNames, topicTitles: {} }))

function who(version: DocVersion): string {
  const name = props.nameOf(version.actor)
  return version.requested_by
    ? t('work.room.doc.historyFor', { who: name, requester: props.nameOf(version.requested_by) })
    : name
}

async function restoreSelected() {
  const target = current.value
  const top = latest.value
  if (!props.restore || !target || !top || target.version === top.version) return
  const id = session
  restoring.value = true
  error.value = null
  try {
    await props.restore(target.version, top.version)
    if (id !== session) return
    emit('restored')
    emit('update:open', false)
  } catch (cause) {
    if (id === session)
      error.value = cause instanceof Error && cause.message ? cause.message : t('work.room.doc.restoreFailed')
  } finally {
    if (id === session) restoring.value = false
  }
}
</script>

<template>
  <v-dialog
    :model-value="open"
    max-width="960"
    scrollable
    :aria-label="t('work.room.doc.history')"
    @update:model-value="emit('update:open', $event)"
  >
    <div class="doc-history">
      <div class="doc-history__head">
        <span class="t-title">{{ t('work.room.doc.history') }}</span>
        <v-spacer />
        <DocEditButton
          v-if="editable && restore && current && latest && current.version !== latest.version"
          strong
          :disabled="restoring"
          @click="restoreSelected"
        >
          {{ t('work.room.doc.restore') }}
        </DocEditButton>
        <button
          type="button"
          class="doc-history__close"
          :aria-label="t('work.room.doc.close')"
          @click="emit('update:open', false)"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </div>
      <div v-if="error" class="doc-history__error" role="alert">{{ error }}</div>
      <div class="doc-history__body">
        <div class="doc-history__preview">
          <MarkdownView v-if="current" class="md-content" :source="current.content" :names="names" />
          <div v-else-if="!loading" class="doc-history__empty">{{ t('work.room.doc.historyEmpty') }}</div>
        </div>
        <ol class="doc-history__list" :aria-label="t('work.room.doc.history')">
          <li v-for="(v, i) in entries" :key="v.version">
            <button
              type="button"
              class="doc-history__item"
              :aria-current="v.version === selected ? 'true' : undefined"
              @click="selected = v.version"
            >
              <span class="doc-history__who">{{ who(v) }}</span>
              <span class="doc-history__when">
                {{ relTime(v.created_at)
                }}<template v-if="i === 0"> · {{ t('work.room.doc.historyCurrent') }}</template>
              </span>
            </button>
          </li>
          <li v-if="cursor !== null && versions.length">
            <button type="button" class="doc-history__more" :disabled="loading" @click="more()">
              {{ t('work.room.doc.historyMore') }}
            </button>
          </li>
        </ol>
      </div>
    </div>
  </v-dialog>
</template>

<style scoped>
.doc-history {
  display: flex;
  flex-direction: column;
  height: min(720px, calc(100vh - 48px));
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
.doc-history__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 12px 24px;
  border-bottom: 1px solid var(--line);
}
.doc-history__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.doc-history__close:hover {
  background: var(--fill);
}
.doc-history__error {
  padding: 8px 24px;
  background: var(--danger-wash);
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-history__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.doc-history__preview {
  flex: 1 1 auto;
  min-width: 0;
  padding: 24px 32px;
  overflow-y: auto;
  color: var(--text);
}
.doc-history__empty {
  color: var(--muted);
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-history__list {
  flex: 0 0 260px;
  margin: 0;
  padding: 8px;
  overflow-y: auto;
  border-left: 1px solid var(--line);
  list-style: none;
}
.doc-history__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  text-align: left;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-history__item:hover,
.doc-history__item[aria-current='true'] {
  background: var(--fill);
}
.doc-history__who {
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-history__when {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.doc-history__more {
  width: 100%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-history__more:hover:not(:disabled) {
  background: var(--fill);
}
@media (max-width: 640px) {
  .doc-history__body {
    flex-direction: column-reverse;
  }
  .doc-history__list {
    flex: 0 0 auto;
    max-height: 40%;
    border-left: none;
    border-bottom: 1px solid var(--line);
  }
}
</style>
