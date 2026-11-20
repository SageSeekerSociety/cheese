<template>
  <AccessView
    :open-scope="space?.openScope ?? null"
    :open-request="space?.openRequest ?? null"
    :saving="saving"
    @save="save"
  />
</template>

<script setup lang="ts">
// 「访问范围」这一栏的容器：把这块板设为只对成员开放，或开放给一组邮箱后缀
// （`PUT /spaces/{id}/access`）。画面在 `AccessView.vue`。
//
// 扩大范围要等平台审核：服务端照旧回这块板，现在的范围不变，带着那条待审的申请；
// 收窄当场生效。回来的空间直接放进 store，画面照它重画。
import type { AccessChange } from './AccessView.vue'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { storeToRefs } from 'pinia'

import AccessView from './AccessView.vue'

import { SpacesApi } from '@/api/spaces'
import { toast } from '@/lib/toast'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const { currentSpace: space } = storeToRefs(spaceStore)

const saving = ref(false)

async function save(change: AccessChange) {
  const id = space.value?.id
  if (!id || saving.value) return
  saving.value = true
  try {
    const { space: saved } = await SpacesApi.setAccess(id, change)
    spaceStore.setSpace(saved)
    toast.success(
      saved.openRequest?.status === 'PENDING' ? t('spaces.access.submitted') : t('spaces.access.saveSuccess')
    )
  } catch {
    // The form keeps what was typed.
    toast.error(t('spaces.access.saveFailed'))
  } finally {
    saving.value = false
  }
}
</script>
