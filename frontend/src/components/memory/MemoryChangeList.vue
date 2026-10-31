<script setup lang="ts">
// 记忆的「最近改动」：每一次改动是谁、从哪条路、什么时候、改了哪一条，点开看差在
// 哪，能恢复的带一个恢复按钮。它不取数：改动列表、谁能恢复、名字都由页面递进来，
// 恢复只往外说一声（emit），写回去由页面做。
import type { MemoryChangeOut } from '../../api/memory'

import { parseDiffLines } from '../../lib/diff'
import { relTime } from '../../lib/relTime'
import BaseButton from '../base/BaseButton.vue'
import ChangesDiff from '../panels/ChangesDiff.vue'

import { t } from '@/i18n'

const props = defineProps<{
  changes: MemoryChangeOut[]
  /** handle → 显示名；不在里面的按 handle 显示。 */
  names: Record<string, string>
  /** 这一条现在恢复得了吗（作用域写不写得动、是不是新建，页面判）。 */
  mayRestore: (change: MemoryChangeOut) => boolean
  /** 正在恢复的那一条：按钮转圈，别的不动。 */
  restoring?: string | null
  /** 还有更早的改动没读到：保留期内的每一条都翻得到。 */
  more?: boolean
  loadingMore?: boolean
}>()

const emit = defineEmits<{ restore: [id: string]; more: [] }>()

function who(change: MemoryChangeOut): string {
  return props.names[change.changed_by] || change.changed_by
}

function action(change: MemoryChangeOut): string {
  if (change.created) return t('project.docs.memoryChangeCreated')
  if (change.deleted) return t('project.docs.memoryChangeDeleted')
  return t('project.docs.memoryChangeEdited')
}
</script>

<template>
  <section class="memory-changes">
    <!-- copy: needs product sign-off -->
    <h2 class="t-title memory-changes__title">{{ t('project.docs.memoryChanges') }}</h2>
    <!-- copy: needs product sign-off -->
    <div v-if="changes.length === 0" class="t-body c-muted py-2">{{ t('project.docs.memoryChangesEmpty') }}</div>
    <div v-for="change in changes" :key="change.id" class="memory-change">
      <div class="d-flex align-start ga-3">
        <v-icon size="16" class="c-muted mt-1">
          {{ change.scope === 'private' ? 'mdi-account-outline' : 'mdi-source-repository' }}
        </v-icon>
        <div class="flex-grow-1 memory-change__body">
          <div class="t-body memory-change__path">{{ change.path }}</div>
          <div class="t-meta c-muted mt-1">
            {{ who(change) }} · {{ t(`project.docs.memoryWriter.${change.writer}`) }} · {{ action(change) }} ·
            {{ change.scope === 'private' ? t('project.docs.memoryUser') : t('project.docs.memoryProject') }} ·
            {{ relTime(change.created_at) }}
          </div>
        </div>
        <BaseButton
          v-if="mayRestore(change)"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-restore"
          :loading="restoring === change.id"
          :disabled="!!restoring && restoring !== change.id"
          @click="emit('restore', change.id)"
        >
          <!-- copy: needs product sign-off -->
          {{ t('project.docs.memoryRestore') }}
        </BaseButton>
      </div>
      <details v-if="change.diff" class="memory-change__more">
        <!-- copy: needs product sign-off -->
        <summary class="t-meta c-muted">{{ t('project.docs.memoryChangeShowDiff') }}</summary>
        <ChangesDiff :lines="parseDiffLines(change.diff)" inline />
      </details>
    </div>
    <BaseButton
      v-if="more"
      kind="ghost"
      size="sm"
      class="memory-changes__more"
      :loading="loadingMore"
      @click="emit('more')"
    >
      <!-- copy: needs product sign-off -->
      {{ t('project.docs.memoryChangesMore') }}
    </BaseButton>
  </section>
</template>

<style scoped>
.memory-changes {
  margin-top: 32px;
}
.memory-changes__title {
  margin-bottom: 8px;
}
.memory-change {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.memory-change__body {
  min-width: 0;
}
.memory-change__path {
  overflow-wrap: anywhere;
}
.memory-change__more {
  margin-top: 8px;
}
.memory-changes__more {
  margin-top: 8px;
}
.memory-change__more > summary {
  cursor: pointer;
}
</style>
