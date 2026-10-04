<script setup lang="ts">
// 一项产物交付过的每一版，最新的在最上面。
//
// 每一版说清三件事：这次改了什么（卡上那句说明）、谁什么时候认的、在哪次对话里做
// 出来的——最后一样能点回那个房间，要问「当时为什么这么改」就去那里。能拿走的在这
// 一行上拿（下载 / 打开），要看它比上一版改了什么也从这一行进去。
import type { ArtifactVersion } from '@/api'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { topicTitle } from '@/lib/topicState'

const props = defineProps<{
  projectId: string
  /** 第一版在前，和接口给的顺序一样。 */
  versions: ArtifactVersion[]
  downloading: string
}>()

const emit = defineEmits<{ download: [version: ArtifactVersion]; compare: [version: ArtifactVersion] }>()

// 一项做了几十版时，人来这里多半只看最近几版。
const SHOWN = 4
const all = ref(false)
const newestFirst = computed(() => [...props.versions].reverse())
const shown = computed(() => (all.value ? newestFirst.value : newestFirst.value.slice(0, SHOWN)))
const hidden = computed(() => newestFirst.value.length - shown.value.length)
const latest = computed(() => newestFirst.value[0]?.card_id)

/** 上一版：有它，且两版是同一种交法，才比得出东西。 */
function previous(version: ArtifactVersion): ArtifactVersion | undefined {
  return props.versions.find((v) => v.number === version.number - 1)
}
</script>

<template>
  <div class="versions">
    <ul v-if="shown.length" class="versions__list">
      <li v-for="version in shown" :key="version.card_id" class="version">
        <span class="version__no t-meta">{{ t('tasks.artifactComparison.version', { number: version.number }) }}</span>
        <div class="version__main">
          <div class="version__subject t-body">
            {{ version.subject || t('tasks.artifact.noSubject') }}
            <span v-if="version.card_id === latest" class="version__current t-meta">{{
              t('tasks.artifact.current')
            }}</span>
          </div>
          <div class="t-meta c-faint">
            <template v-if="version.delivered_at">{{ relTime(version.delivered_at) }}</template>
            <template v-if="version.decided_by">
              ·
              <i18n-t keypath="tasks.artifact.acceptedBy" tag="span">
                <template #who><UserRef :handle="version.decided_by" /></template>
              </i18n-t>
            </template>
            <template v-if="version.room">
              · {{ t('tasks.artifact.from') }}
              <router-link
                :to="{ name: 'workspace-topic', params: { projectId, topicId: version.room.id } }"
                class="version__room"
                >{{ t('work.topic.quoted', { title: topicTitle(version.room) }) }}</router-link
              >
            </template>
          </div>
          <div class="version__actions">
            <BaseButton
              v-if="version.kind === 'file'"
              size="sm"
              :loading="downloading === version.card_id"
              @click="emit('download', version)"
            >
              {{ t('tasks.artifact.download') }}
            </BaseButton>
            <BaseButton
              v-else-if="version.kind === 'link' && version.url"
              size="sm"
              :href="version.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ t('tasks.artifact.open') }}
            </BaseButton>
            <!-- 交出去的是一次合并，或者这一版早于交付物留存：两种都没有文件可给，
                 而它们不是同一件事，所以话也不一样。 -->
            <span v-else class="t-meta c-faint version__none">
              {{ version.kind === 'merge' ? t('tasks.artifact.mergeOnly') : t('tasks.artifact.notRetained') }}
            </span>
            <BaseButton v-if="previous(version)" size="sm" @click="emit('compare', version)">
              {{ t('tasks.artifact.compareWith', { number: version.number - 1 }) }}
            </BaseButton>
          </div>
        </div>
      </li>
    </ul>
    <BaseEmptyState v-else size="inline" class="versions__empty" :title="t('tasks.artifact.noVersions')" />
    <BaseButton v-if="hidden > 0" size="sm" @click="all = true">
      {{ t('tasks.artifact.showEarlier', { n: hidden }) }}
    </BaseButton>
  </div>
</template>

<style scoped>
.versions {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.versions__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
}
.version {
  display: flex;
  gap: 12px;
  padding: 12px 12px 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
/* 版号自成一列、不折行：一眼扫下来是 7、6、5。 */
.version__no {
  flex: none;
  min-width: 52px;
  padding-top: 1px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.version__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.version__subject {
  color: var(--ink);
  overflow-wrap: anywhere;
}
.version__current {
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  white-space: nowrap;
}
.version__room {
  color: var(--accent-ink);
  text-decoration: none;
}
.version__room:hover {
  text-decoration: underline;
}
.version__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-left: -8px;
}
.version__none {
  padding: 0 8px;
}
.versions__empty {
  padding: 24px 0;
}
</style>
