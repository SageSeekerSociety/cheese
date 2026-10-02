<template>
  <SpaceGuidanceView :teaching="space?.teaching" :saving="saving" @save="save" />
</template>

<script setup lang="ts">
// 空间设置里「给 AI 队友的指导」这一栏的容器（#944）：读这块板、把这份默认整份写
// 回服务端。画面在 `SpaceGuidanceView.vue`，它只收 props、只报 save。
//
// 读写都搭在已有的实体接口上：写走 `PATCH /spaces/{id}`（判据与基本信息同一份 ——
// 谁能改这块板，谁才能写这份默认），读走 `GET /spaces/{id}`。整份提交，不在本地留
// 一份自己的副本：写回去的那一版就是服务端认的，保存完照它重填。
import type { SpaceTeaching } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import SpaceGuidanceView from './SpaceGuidanceView.vue'

import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space } = storeToRefs(spaceStore)

const saving = ref(false)

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
