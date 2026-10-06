<template>
  <ManageDomainGroupsView
    v-model:open="dialogOpen"
    :domain-groups="domainGroups"
    :loading="loading"
    :saving="saving"
    @submit="submitGroup"
    @delete="deleteGroup"
  />
</template>

<script setup lang="ts">
// 域名组管理这一页的容器：按空间读列表、增删改、失败时重读。画面在
// `ManageDomainGroupsView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { DomainGroupValues } from './ManageDomainGroupsView.vue'

import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import ManageDomainGroupsView from './ManageDomainGroupsView.vue'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()

const spaceId = Number(route.params.spaceId)

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { domainGroups } = storeToRefs(spaceStore)

const loading = ref(false)
const saving = ref(false)
const dialogOpen = ref(false)

async function fetchDomainGroups() {
  loading.value = true
  try {
    await spaceData.fetchDomainGroups(spaceId)
  } catch {
    // handled silently
  } finally {
    loading.value = false
  }
}

const submitGroup = async (payload: { id: number | null; values: DomainGroupValues }) => {
  saving.value = true
  try {
    const body = {
      name: payload.values.name,
      description: payload.values.description || null,
      domains: payload.values.domains.filter((d) => d.trim() !== ''),
    }

    if (payload.id !== null) {
      await SpacesApi.updateDomainGroup(spaceId, payload.id, body)
    } else {
      await SpacesApi.createDomainGroup(spaceId, body)
    }
    dialogOpen.value = false
    await fetchDomainGroups()
  } catch {
    // handled by API layer
  } finally {
    saving.value = false
  }
}

async function deleteGroup(groupId: number) {
  try {
    await SpacesApi.deleteDomainGroup(spaceId, groupId)
    await fetchDomainGroups()
  } catch {
    // handled by API layer
  }
}

onMounted(() => {
  fetchDomainGroups()
})
</script>
