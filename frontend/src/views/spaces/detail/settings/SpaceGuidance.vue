<template>
  <SpaceGuidanceView
    :teaching="space?.teaching"
    :materials="materials"
    :materials-loading="materialsLoading"
    :saving="saving"
    @save="save"
  />
</template>

<script setup lang="ts">
// 空间设置里「给 AI 队友的指导」这一栏的容器（#944）：读这块板、把这份默认整份写
// 回服务端。画面在 `SpaceGuidanceView.vue`，它只收 props、只报 save。
//
// 读写都搭在已有的实体接口上：写走 `PATCH /spaces/{id}`（判据与基本信息同一份 ——
// 谁能改这块板，谁才能写这份默认），读走 `GET /spaces/{id}`。整份提交，不在本地留
// 一份自己的副本：写回去的那一版就是服务端认的，保存完照它重填。
//
// 参考资料那一格的候选走 `GET /spaces/{id}/materials`（资料库那一页同一份清单），
// 含「仅管理员」档 —— 哪一档能进选择器由 `TeachingMaterialPicker` 一处判。
import type { SpaceMaterial, SpaceTeaching } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import SpaceGuidanceView from './SpaceGuidanceView.vue'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space } = storeToRefs(spaceStore)

const saving = ref(false)

/** 这块板资料库里现在有哪些文件。取不到就是空清单 —— 选择器那边会说「还没有文件」，
 *  不拦着人保存这份指导（指导本身跟课件是两件事）。 */
const materials = ref<SpaceMaterial[]>([])
const materialsLoading = ref(false)

async function refreshMaterials(spaceId?: number) {
  if (!spaceId) {
    materials.value = []
    return
  }
  materialsLoading.value = true
  try {
    const res = await SpacesApi.listMaterials(spaceId)
    // 期间换了块板/切走了页面就不要再落下来 —— 落下来的会是别人的清单。
    if (space.value?.id !== spaceId) return
    materials.value = res.data.materials ?? []
  } catch {
    materials.value = []
  } finally {
    materialsLoading.value = false
  }
}

watch(
  () => space.value?.id,
  (id) => void refreshMaterials(id),
  { immediate: true }
)

async function save(teaching: SpaceTeaching) {
  const id = space.value?.id
  if (!id || saving.value) return
  saving.value = true
  try {
    await spaceData.updateSpace(id, { teaching }, false)
    toast.success(t('spaces.guidance.saveSuccess'))
  } catch {
    // 失败的提示在这里给；表单留着，填的内容不丢。
    toast.error(t('spaces.guidance.saveFailed'))
  } finally {
    saving.value = false
  }
}
</script>
