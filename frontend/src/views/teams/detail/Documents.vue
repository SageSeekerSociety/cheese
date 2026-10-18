<script setup lang="ts">
// 团队的「文档」（#3774）：只属于团队、不属于哪个项目的文档。个人名下就是自己的文档。
// 团队成员都能读写；行上写谁什么时候改的，点开整页打开。这一页取数、新建、跳转，
// 画面在同目录的 `DocumentsView.vue`。
import type { ListedDocument } from '@/api/projectDocuments'
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { ProjectMemberRow } from '@/cx_types'

import { computed, inject, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import DocumentsView from './DocumentsView.vue'

import { createTeamDocument, listTeamDocuments } from '@/api/teamDocuments'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const router = useRouter()
const teamData = inject(teamDataInjectionKey, ref())
const docs = ref<ListedDocument[]>([])
const members = ref<ProjectMemberRow[]>([])
const loading = ref(false)
const making = ref(false)
const error = ref('')

const rows = computed<LibraryRow[]>(() => docs.value.map((doc) => ({ key: doc.id, rank: doc.rank, doc })))

watch(
  () => teamData.value?.id,
  async (teamId) => {
    if (!teamId) return
    loading.value = true
    error.value = ''
    try {
      const [page, roster] = await Promise.all([listTeamDocuments(teamId), TeamsApi.getMembers(teamId)])
      docs.value = page.data
      members.value = roster.members.map((m) => ({
        user_handle: m.user.username,
        name: m.user.nickname,
        name_source: 'human',
      }))
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t('teams.docs.loadFailed')
    } finally {
      loading.value = false
    }
  },
  { immediate: true }
)

function open(id: string) {
  void router.push({ name: 'TeamsDocument', params: { handle: teamData.value?.handle ?? '', docId: id } })
}

async function make() {
  const teamId = teamData.value?.id
  if (!teamId) return
  making.value = true
  error.value = ''
  try {
    open((await createTeamDocument(teamId)).id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('teams.docs.newFailed')
  } finally {
    making.value = false
  }
}
</script>

<template>
  <DocumentsView
    :rows="rows"
    :members="members"
    :loading="loading"
    :making="making"
    :error="error"
    @open="open"
    @make="make"
  />
</template>
