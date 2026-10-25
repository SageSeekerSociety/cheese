<script setup lang="ts">
// 团队（或自己名下）的一份文档，整页打开（#3774）。和资料库里打开一份是同一个面板，
// 只是上一级是团队的「资料库」，名册是团队成员，没有芝士：这一步项目里的芝士读不到团队文档。
// 这一页取数、删除、移动；画面在同目录的 `TeamDocumentView.vue`。
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { ProjectMemberRow } from '@/cx_types'

import { computed, inject, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useDocumentMove } from '@/composables/useDocumentMove'
import { useDocumentShare } from '@/composables/useDocumentShare'
import { usePanelDocWiring } from '@/composables/usePanelDocWiring'

import TeamDocumentView from './TeamDocumentView.vue'

import { deleteDocument, getDocumentAbout } from '@/api/projectDocuments'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { docAuthors } from '@/lib/docAuthors'
import { useDialog } from '@/plugins/dialog'
import { useAgentContext } from '@/stores/agent'

const props = defineProps<{ handle: string; docId: string }>()
const router = useRouter()
const dialog = useDialog()
const mover = useDocumentMove((to) => void router.push(to))
const sharer = useDocumentShare()
const teamData = inject(teamDataInjectionKey, ref())
/** 页头的上一级：自己名下是「我的资料库」，团队是「某团队的资料库」。 */
const parentLabel = computed(() =>
  teamData.value?.personal ? t('teams.docs.mine') : t('teams.docs.ofTeam', { name: teamData.value?.name ?? '' })
)

const document = ref<PanelDocument | null>(null)
const members = ref<ProjectMemberRow[]>([])
const error = ref('')
// 文档那一格的取数：团队文档没有芝士，名册是团队成员。
const wiring = usePanelDocWiring(
  reactive({ topic: null, document, activityTick: 0, agentName: '', agentHandle: null, members })
)
// 作者：团队名册上认人；写它的 AI 队友不在团队名册上，写它的通称。
const authors = computed(() =>
  document.value ? docAuthors(document.value, members.value, t('work.room.defaultAgentName')) : []
)
const parent = computed(() => ({
  label: parentLabel.value,
  to: { name: 'TeamsDetailLibrary', params: { handle: props.handle } },
}))

watch(
  () => [props.handle, props.docId] as const,
  async ([handle, docId]) => {
    error.value = ''
    try {
      void handle
      const about = await getDocumentAbout(docId)
      document.value = {
        id: about.id,
        projectId: '',
        title: about.title ?? '',
        created_by: about.created_by,
        drafted_by: about.drafted_by,
      }
      const roster = teamData.value ? await TeamsApi.getMembers(teamData.value.id) : { members: [] }
      members.value = roster.members.map((m) => ({
        user_handle: m.user.username,
        name: m.user.nickname,
        name_source: 'human',
      }))
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause)
    }
  },
  { immediate: true }
)

// 芝士读到的「当前页面」是这份文档：团队的文档芝士没有工具去读，后端把正文写给它。
useAgentContext(() => ({
  page: { kind: 'doc', id: props.docId },
  label: document.value
    ? t('tasks.assistant.page.doc', { name: document.value.title || t('work.room.doc.untitled') })
    : undefined,
}))

/** 删除：先问一句，删了回到「文档」那一页。 */
async function remove() {
  const confirmed = await dialog
    .confirm(t('teams.docs.deleteBody'), {
      title: t('teams.docs.deleteTitle', { name: document.value?.title || t('work.room.doc.untitled') }),
      confirmLabel: t('teams.docs.delete'),
      danger: true,
    })
    .wait()
    .catch(() => false)
  if (!confirmed) return
  try {
    await deleteDocument(props.docId)
    void router.push({ name: 'TeamsDetailLibrary', params: { handle: props.handle } })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}
</script>

<template>
  <TeamDocumentView
    v-model:moving="mover.state.open"
    v-model:sharing="sharer.state.open"
    :share="{
      title: sharer.state.title,
      loading: sharer.state.loading,
      mayShare: sharer.state.mayShare,
      url: sharer.url.value,
      busy: sharer.state.busy,
      error: sharer.state.error,
    }"
    :document="document"
    :authors="authors"
    :wiring="wiring"
    :error="error"
    :parent="parent"
    :move="{
      title: mover.state.title,
      places: mover.places.value,
      current: mover.state.current,
      saving: mover.state.saving,
      error: mover.state.error,
    }"
    @delete="remove"
    @ask-move="mover.open(docId)"
    @move="mover.move"
    @ask-share="sharer.open(docId)"
    @share="sharer.share"
    @unshare="sharer.unshare"
    @copy="sharer.copy"
  />
</template>
