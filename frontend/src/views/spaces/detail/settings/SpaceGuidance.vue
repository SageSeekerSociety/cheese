<script setup lang="ts">
// 空间设置里「给 AI 队友的指导」这一栏：这块板的默认（#944）。四级继承的最外层
// —— 空间 → 项目集 → 题目 → 项目，整份替换、不深合；这里留空的那几格就是
// 「没说」，下面哪一层说了就听哪一层。
//
// 读写都搭在已有的实体接口上：写走 `PATCH /spaces/{id}`（判据与基本信息同一份 ——
// 谁能改这块板，谁才能写这份默认），读走 `GET /spaces/{id}`。整份提交，不在本地
// 留一份自己的副本：写回去的那一版就是服务端认的，保存完照它重填。
import type { SpaceTeaching } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import TeachingFields from '@/components/common/TeachingFields.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space } = storeToRefs(spaceStore)

const saving = ref(false)
const draft = ref<SpaceTeaching>({})

// 表单跟着这块板走：读回来、换了板、保存后重新读回来，都照它重填一遍。
watch(
  () => space.value?.teaching,
  (teaching) => {
    draft.value = teaching ?? {}
  },
  { immediate: true, deep: true }
)

async function save() {
  const id = space.value?.id
  if (!id || saving.value) return
  saving.value = true
  try {
    // 整份替换：六格全空 = 清掉这块板的默认，于是没有默认。
    await spaceData.updateSpace(id, { teaching: draft.value }, false)
    toast.success(t('spaces.guidance.saveSuccess'))
  } catch {
    // 失败的提示在这里给；表单留着，填的内容不丢。
    toast.error(t('spaces.guidance.saveFailed'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SettingsToolbar />

  <form class="settings-card" novalidate @submit.prevent="save">
    <div class="settings-card__title">{{ t('spaces.guidance.title') }}</div>
    <p class="settings-card__desc">{{ t('spaces.guidance.intro') }}</p>
    <div class="guidance__body">
      <TeachingFields v-model="draft" />
    </div>
    <div class="settings-foot">
      <v-btn color="primary" variant="flat" :loading="saving" @click="save">
        {{ t('spaces.guidance.save') }}
      </v-btn>
    </div>
  </form>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.guidance__body {
  padding: 8px 24px 20px;
}

.settings-foot {
  display: flex;
  justify-content: flex-end;
  padding: 16px 24px;
  border-top: 1px solid var(--line);
}

@media (max-width: 599.98px) {
  .guidance__body {
    padding: 8px 16px 16px;
  }

  .settings-foot {
    padding: 16px;
  }
}
</style>
