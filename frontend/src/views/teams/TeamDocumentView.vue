<script setup lang="ts">
// 团队（或自己名下）一份文档整页的画面：页头写「某团队的文档 / 标题」，文档自己的顶栏
// 摆在页头右边，「移动到…」的框也在这里。取数、删除、移动的动作在同目录的
// `TeamDocument.vue`；文档那一格的取数由它交进来，面板只吃 props。
import type { MovePlace } from '@/components/library/DocMoveDialog.vue'
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { PanelDocWiring } from '@/composables/usePanelDocWiring'
import type { DocAuthor } from '@/lib/docAuthors'
import type { NavTarget } from '@/lib/navTarget'

import { useDisplay } from 'vuetify'

import AppPage from '@/components/common/AppPage.vue'
import DocMoveDialog from '@/components/library/DocMoveDialog.vue'
import DocShareDialog from '@/components/library/DocShareDialog.vue'
import PanelDoc from '@/components/panels/PanelDoc.vue'
import { t } from '@/i18n'

defineProps<{
  /** 还在读的时候是 null。 */
  document: PanelDocument | null
  /** 文档的作者，画在大标题下面。 */
  authors: DocAuthor[]
  wiring: PanelDocWiring
  error: string
  parent: { label: string; to: NavTarget }
  move: {
    title: string
    places: MovePlace[]
    current: { teamId: number; projectId: string | null }
    saving: boolean
    error: string
  }
  share: {
    title: string
    loading: boolean
    mayShare: boolean
    url: string | null
    busy: boolean
    error: string
  }
}>()
const moving = defineModel<boolean>('moving', { required: true })
const sharing = defineModel<boolean>('sharing', { required: true })

const emit = defineEmits<{
  delete: []
  'ask-move': []
  move: [to: { teamId: number; projectId: string | null }]
  'ask-share': []
  share: []
  unshare: []
  copy: []
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <AppPage
    :title="document ? document.title || t('work.room.doc.untitled') : parent.label"
    :parent="parent"
    width="full"
    fill
  >
    <template v-if="mdAndUp" #controls>
      <div id="team-doc-bar" class="team-doc__bar" />
    </template>
    <p v-if="error" role="alert" class="t-body c-danger pa-6">{{ error }}</p>
    <PanelDoc
      v-if="document"
      class="team-doc"
      :doc-panel="wiring.docPanel"
      :doc-threads="wiring.docThreads"
      :doc-people="wiring.docPeople"
      :comment-outside="wiring.commentOutside"
      :topic="null"
      :document="document"
      :activity-tick="0"
      :topic-list="[]"
      agent-name=""
      :agent-handle="null"
      :authors="authors"
      :bar-to="mdAndUp ? '#team-doc-bar' : undefined"
      @delete="emit('delete')"
      @move="emit('ask-move')"
      @share="emit('ask-share')"
    />
    <div v-else-if="!error" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
  </AppPage>
  <DocMoveDialog
    v-model="moving"
    :title="move.title"
    :places="move.places"
    :current="move.current"
    :saving="move.saving"
    :error="move.error"
    @move="emit('move', $event)"
  />
  <DocShareDialog
    v-model="sharing"
    :title="share.title"
    :loading="share.loading"
    :may-share="share.mayShare"
    :url="share.url"
    :busy="share.busy"
    :error="share.error"
    @share="emit('share')"
    @unshare="emit('unshare')"
    @copy="emit('copy')"
  />
</template>

<style scoped>
.team-doc__bar {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}
/* 顶栏在页头这一格里由按钮撑开：这一格本身按内容定宽，顶栏若按自己的宽度量（它的
   `container-type`），两边都等对方给宽度，量出来是 0，整排按钮看不见。这里只在宽屏上
   放顶栏，按钮上的字不必收起。 */
.team-doc__bar :deep(.doc-top-bar) {
  container-type: normal;
}
.team-doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
