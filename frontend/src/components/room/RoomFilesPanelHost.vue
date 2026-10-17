<script setup lang="ts">
// 工作面板「文件」那一格：取这段对话的附件和文档，保存到资料库。只有这段对话里的人看得
// 到的（私密频道、私聊），保存之前先说清保存后谁会看到。
import type { ConversationFile } from '@/composables/useConversationFiles'

import { computed, getCurrentInstance, ref, toRef } from 'vue'

import { useConversationFiles } from '@/composables/useConversationFiles'
import { useKeepAudience } from '@/composables/useKeepAudience'

import PanelRoomFiles from '../panels/PanelRoomFiles.vue'

import KeepToLibraryConfirm from './KeepToLibraryConfirm.vue'

import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  topicId: string | null
  /** 只有这段对话里的人看得到：私密频道或私聊。 */
  seatsOnly: boolean
}>()

const emit = defineEmits<{
  /** 打开一份：附件按路径开，文档按 id 开，由工作面板决定开在哪一格。 */
  open: [row: ConversationFile]
}>()

const { rows, truncated, loading, error, keeping, load, keep } = useConversationFiles(toRef(props, 'topicId'))

const app = getCurrentInstance()?.appContext.config.globalProperties
const workspace = app?.$pinia ? useWorkspaceStore() : null
/** handle → 名字，署名用：和「定时与触发」那一格一样从工作区的名册里查。 */
const names = computed(() =>
  Object.fromEntries(
    (workspace?.members ?? []).map((m) => [m.user_handle, m.name]).filter((e): e is [string, string] => Boolean(e[1]))
  )
)

const asking = ref<ConversationFile | null>(null)
const { people, projectPeople } = useKeepAudience(
  () => props.topicId,
  () => asking.value !== null
)
const failed = ref<string | null>(null)

async function store(row: ConversationFile) {
  failed.value = null
  try {
    await keep(row)
  } catch (e) {
    failed.value = e instanceof Error && e.message ? e.message : t('work.room.files.keepFailed')
  }
}

function requestKeep(row: ConversationFile) {
  if (props.seatsOnly) {
    asking.value = row
    return
  }
  void store(row)
}

async function confirmKeep() {
  const row = asking.value
  asking.value = null
  if (row) await store(row)
}

defineExpose({ reload: load })
</script>

<template>
  <div class="room-files-host">
    <p v-if="failed" class="room-files-host__failed t-meta" role="alert">{{ failed }}</p>
    <PanelRoomFiles
      :rows="rows"
      :names="names"
      :loading="loading"
      :error="error"
      :keeping="keeping"
      :truncated="truncated"
      @open="emit('open', $event)"
      @keep="requestKeep"
      @retry="load"
    />
    <KeepToLibraryConfirm
      :name="asking?.name ?? null"
      :people="people"
      :project-people="projectPeople"
      @confirm="confirmKeep"
      @cancel="asking = null"
    />
  </div>
</template>

<style scoped>
.room-files-host {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.room-files-host__failed {
  padding: 8px 16px 0;
  color: var(--danger-ink);
}
</style>
