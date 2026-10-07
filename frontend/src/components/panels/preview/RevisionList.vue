<script setup lang="ts">
// 一份 .docx 的修订，逐条处理。
//
// 改别人的文档要留修订，所以一份芝士改过的 .docx 里带着 `<w:ins>` / `<w:del>`。
// 旁边那一页已经把它们画出来了——LibreOffice 会渲染修订（实测：插入和删除的文字都
// 出现在 PDF 里）。所以这份清单不是为了让人看见改动，是为了让人**处理**改动：逐条
// 接受或拒绝，不用先装一个 Word。
//
// 每一条都带作者，而且一条都不过滤。用户传来的文档里本来就可能有别人未接受的修订，
// 在自己的文档里接受同事的一处改动是件平常事；不平常的是不知不觉地接受了它。
//
// 预览和改动两格用的是同一个组件：一处修订算一条这件事只能有一个答案，两份实现走散
// 的表现是读者点了第 2 条、生效的是第 3 条。
//
// 清单从哪来、接受/拒绝发给谁，都在 `composables/useDocumentRevisions.ts` 里：两格各
// 调一次，包成 `revs` 递进来。这一只只画，处理哪一条按 `decide` 事件发上去。
import type { DocumentRevisionsBundle } from '../../../composables/useDocumentRevisions'
import type { DocumentRevision } from '../../../cx_types'

import { isLibraryPath } from '../../../lib/library'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// **只吃 props**：清单、只读、读失败的原话都在 `composables/useDocumentRevisions.ts` 里
// 取（改动那一格和预览那一格各调一次，落在 `components/work/PanelChangesHost.vue` /
// `PanelPreviewHost.vue`），这一只只决定画成什么样。`components/panels/**` 下每个 SFC
// 都是「场景」，场景不取数。
const props = defineProps<{
  /** 这一份修订的取数（`composables/useDocumentRevisions.ts` 那一包）。 */
  revs: DocumentRevisionsBundle
  /** 在读哪一份：只用来选那句「不能改」的说法（资料库里的原件，还是别的只读）。 */
  path?: string | null
}>()

function reads(row: DocumentRevision): string {
  if (row.kind === 'replace') return t('work.room.revisions.replaced', { removed: row.removed, added: row.added })
  if (row.kind === 'insert') return t('work.room.revisions.inserted', { added: row.added })
  return t('work.room.revisions.deleted', { removed: row.removed })
}
</script>

<template>
  <!-- 一根柱子，两种内容：清单，或者一句「没读出来」。读不出清单时文档照旧显示——
       丢掉的是清单，而那份文档仍然是这个文件现在的样子。 -->
  <aside v-if="props.revs.error.value || props.revs.revisions.value.length" class="revs" data-testid="revisions">
    <v-alert v-if="props.revs.error.value" type="warning" density="compact" class="mb-2">
      {{ props.revs.error.value }}
    </v-alert>

    <div v-if="props.revs.revisions.value.length" class="revs__bar">
      <span class="revs__count t-eyebrow">
        {{ t('work.room.revisions.count', { count: props.revs.revisions.value.length }) }}
      </span>
      <v-spacer />
      <BaseButton
        v-if="!props.revs.readOnly.value"
        kind="ghost"
        size="sm"
        :disabled="props.revs.deciding.value > 0"
        @click="props.revs.decide({ accept: props.revs.revisions.value.map((r) => r.number) })"
      >
        {{ t('work.room.revisions.acceptAll') }}
      </BaseButton>
      <BaseButton
        v-if="!props.revs.readOnly.value"
        kind="ghost"
        size="sm"
        :disabled="props.revs.deciding.value > 0"
        @click="props.revs.decide({ reject: props.revs.revisions.value.map((r) => r.number) })"
      >
        {{ t('work.room.revisions.rejectAll') }}
      </BaseButton>
    </div>

    <p v-if="props.revs.readOnly.value && props.revs.revisions.value.length" class="revs__note t-meta">
      {{
        isLibraryPath(props.path ?? '')
          ? t('work.room.revisions.libraryReadOnly')
          : t('work.room.revisions.readOnlyNote')
      }}
    </p>

    <ul v-if="props.revs.revisions.value.length" class="revs__list">
      <li v-for="row in props.revs.revisions.value" :key="row.number" class="revs__item">
        <div class="revs__what">{{ reads(row) }}</div>
        <div class="revs__who t-meta">
          {{
            t('work.room.revisions.where', {
              paragraph: row.paragraph,
              author: row.author || t('work.room.revisions.anonymous'),
            })
          }}
        </div>
        <div v-if="!props.revs.readOnly.value" class="revs__acts">
          <BaseButton
            kind="ghost"
            size="sm"
            :disabled="props.revs.deciding.value > 0"
            @click="props.revs.decide({ accept: [row.number] })"
          >
            {{ t('work.room.revisions.accept') }}
          </BaseButton>
          <BaseButton
            kind="ghost"
            size="sm"
            :disabled="props.revs.deciding.value > 0"
            @click="props.revs.decide({ reject: [row.number] })"
          >
            {{ t('work.room.revisions.reject') }}
          </BaseButton>
        </div>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.revs {
  flex: none;
  width: 236px;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px 12px;
  border-left: 1px solid var(--line);
  background: var(--surface);
}
.revs__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
.revs__count {
  color: var(--muted);
}
.revs__note {
  margin: 0 0 8px;
  color: var(--faint);
}
.revs__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.revs__item {
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.revs__what {
  font-size: 13px;
  color: var(--text);
  word-break: break-word;
}
.revs__who {
  margin-top: 2px;
  color: var(--faint);
}
.revs__acts {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

/* 窄了它落到下方（宿主把 `.doc__body` / `.doc-view__body` 改成竖排），所以左边那条
   界线要换成上边那条。
   判的是宿主那一格有多宽，不是窗口：`@media` 在这里问错了人——窗口 1100 的时候这一
   格可能只有 600。宿主自己声明容器（`PanelPreviewView` 的 `.panel-preview`、
   `PanelChangesView` 的 `.panel-changes`）。 */
@container (max-width: 720px) {
  .revs {
    width: auto;
    max-height: 38%;
    border-left: none;
    border-top: 1px solid var(--line);
  }
}
</style>
