<script setup lang="ts">
// 项目设置里的「频道」：新建、改名、归档、取消归档都在这里。侧栏上不放「新建频道」
// ——频道少而稳定，新建是一年做几次的事；要做成的一件事建成任务，不为它开频道。
//
// 建好就打开它（去哪儿由页面决定）。数据就是侧栏那一份（工作区 store）：这一页叠在项目上面，改了什么侧栏当场跟着变，
// 不需要再拉一次。
import type { Topic } from '@/cx_types'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { inferTopicKind } from '@/lib/topicTree'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()

const store = useWorkspaceStore()
const emit = defineEmits<{ (e: 'open-channel', topic: Topic): void }>()

const mine = computed<Topic[]>(() => store.topics.filter((topic) => topic.project_id === props.projectId))
const root = computed(() => mine.value.find((topic) => inferTopicKind(topic) === 'root') ?? null)
const active = computed(() => mine.value.filter((topic) => topic.status !== 'archived' && topic.id !== root.value?.id))
const archived = computed(() => mine.value.filter((topic) => topic.status === 'archived'))

const draft = ref('')
const creating = ref(false)
async function create() {
  const title = draft.value.trim()
  if (!title || creating.value) return
  creating.value = true
  try {
    const topic = await store.create(title)
    if (!topic) return
    draft.value = ''
    emit('open-channel', topic)
  } finally {
    creating.value = false
  }
}

const renamingId = ref<string | null>(null)
const renameDraft = ref('')
function startRename(topic: Topic) {
  renamingId.value = topic.id
  renameDraft.value = topic.title
}
async function commitRename(topic: Topic) {
  if (renamingId.value !== topic.id) return
  renamingId.value = null
  const title = normalizeTopicTitle(renameDraft.value, topic.title)
  if (title) await store.renameTopic(topic.id, title)
}
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.projectSettings.channels.intro') }}</p>

    <form class="channel-new" data-testid="channel-new" @submit.prevent="create">
      <v-text-field
        v-model="draft"
        :label="t('work.projectSettings.channels.nameLabel')"
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <BaseButton type="submit" kind="primary" :loading="creating" :disabled="!draft.trim() || creating">
        {{ t('work.projectSettings.channels.create') }}
      </BaseButton>
    </form>

    <ul class="channel-list" :aria-label="t('work.projectSettings.channels.activeLabel')">
      <li v-if="root" class="channel-row">
        <v-icon size="16" class="channel-row__glyph" icon="mdi-pound" />
        <span class="channel-row__name">{{ topicTitle(root) }}</span>
        <span class="t-meta c-faint">{{ t('work.projectSettings.channels.rootNote') }}</span>
      </li>
      <li v-for="topic in active" :key="topic.id" class="channel-row" :data-channel="topic.id">
        <v-icon size="16" class="channel-row__glyph" icon="mdi-pound" />
        <v-text-field
          v-if="renamingId === topic.id"
          v-model="renameDraft"
          class="channel-row__field"
          :maxlength="TOPIC_TITLE_MAX_LENGTH"
          density="compact"
          variant="outlined"
          hide-details
          autofocus
          autocomplete="off"
          @keyup.enter="commitRename(topic)"
          @keyup.esc="renamingId = null"
          @blur="commitRename(topic)"
        />
        <span v-else class="channel-row__name" data-user-content>{{ topicTitle(topic) }}</span>
        <BaseButton kind="ghost" size="sm" @click="startRename(topic)">
          {{ t('work.projectSettings.channels.rename') }}
        </BaseButton>
        <BaseButton kind="ghost" size="sm" @click="store.archive(topic.id)">
          {{ t('work.projectSettings.channels.archive') }}
        </BaseButton>
      </li>
    </ul>

    <template v-if="archived.length">
      <h2 class="t-eyebrow c-muted mt-6 mb-2">{{ t('work.projectSettings.channels.archivedLabel') }}</h2>
      <ul class="channel-list" :aria-label="t('work.projectSettings.channels.archivedLabel')">
        <li v-for="topic in archived" :key="topic.id" class="channel-row" :data-channel="topic.id">
          <v-icon size="16" class="channel-row__glyph" icon="mdi-pound" />
          <span class="channel-row__name c-muted" data-user-content>{{ topicTitle(topic) }}</span>
          <BaseButton kind="ghost" size="sm" @click="store.unarchive(topic.id)">
            {{ t('work.projectSettings.channels.unarchive') }}
          </BaseButton>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.channel-new {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.channel-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.channel-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 4px 12px;
}
.channel-row + .channel-row {
  border-top: 1px solid var(--line);
}
.channel-row__glyph {
  flex: none;
  color: var(--faint);
}
.channel-row__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.channel-row__field {
  flex: 1 1 auto;
}
</style>
