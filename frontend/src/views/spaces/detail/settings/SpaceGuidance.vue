<template>
  <SpaceGuidanceView
    :teaching="space?.teaching"
    :materials="materials"
    :materials-state="materialsState"
    :library-to="libraryTo"
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
import type { SpaceTeaching } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'

import SpaceGuidanceView from './SpaceGuidanceView.vue'

import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space } = storeToRefs(spaceStore)

const saving = ref(false)

/** 参考资料那一格的候选。读不出来不拦着人保存这份指导（指导本身跟课件是两件事），
 *  选择器那边会说清是读不出来，而不是把已经引用的编号判成失效。 */
const { materials, state: materialsState } = useSpaceMaterials(computed(() => space.value?.id))

/** 选择器里那条「上传到资料库」跳这儿。这块板还没读出来时不给，也就没有那一条。 */
const libraryTo = computed(() => (space.value ? spaceLibraryPath(space.value.id) : undefined))

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
