<script setup lang="ts">
// 资料库里打开的一份文档：整页是它，页头写「资料库 / 标题」，文档自己的顶栏（在线的
// 人、评论、芝士、⋯）摆在页头右边。和对话旁边的文档是同一个面板，只是直接给编号。
import type { PanelDocument } from '../../composables/usePanelDoc'
import type { ProjectMemberRow, Topic } from '../../cx_types'

import { useDisplay } from 'vuetify'

import PanelDoc from '../panels/PanelDoc.vue'

import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'

defineProps<{
  projectId: string
  /** 还在读的时候是 null。 */
  document: PanelDocument | null
  error: string
  agentName: string
  agentHandle: string | null
  members: ProjectMemberRow[]
  topicList: Topic[]
}>()

const emit = defineEmits<{
  (e: 'titled', title: string): void
  (e: 'delete'): void
  (e: 'open-topic', topicId: string): void
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <AppPage
    :title="document ? document.title || t('work.room.doc.untitled') : t('navigation.project.library')"
    :parent="{ label: t('navigation.project.library'), to: { name: 'project-library', params: { projectId } } }"
    width="full"
    fill
  >
    <template v-if="mdAndUp" #controls>
      <div id="library-doc-bar" class="library-doc__bar" />
    </template>
    <p v-if="error" role="alert" class="t-body c-danger pa-6">{{ error }}</p>
    <PanelDoc
      v-if="document"
      class="library-doc"
      :bar-to="mdAndUp ? '#library-doc-bar' : undefined"
      :topic="null"
      :document="document"
      :activity-tick="0"
      :agent-name="agentName"
      :agent-handle="agentHandle"
      :members="members"
      :topic-list="topicList"
      @titled="emit('titled', $event)"
      @delete="emit('delete')"
      @open-topic="emit('open-topic', $event)"
    />
    <div v-else-if="!error" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
  </AppPage>
</template>

<style scoped>
.library-doc__bar {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}
.library-doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
