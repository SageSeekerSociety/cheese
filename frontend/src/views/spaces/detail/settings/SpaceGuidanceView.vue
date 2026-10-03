<script setup lang="ts">
// 空间设置里「给 AI 队友的指导」这一栏的画面：六格表单 + 保存。读写都不在这里
// —— 容器 `SpaceGuidance.vue` 把这块板的那份默认递进来，接住「保存」去办。
//
// 这一栏是四级继承的最外层（空间 → 项目集 → 题目 → 项目，整份替换、不深合）：
// 这里留空的那几格就是「没说」，下面哪一层说了就听哪一层。
import type { SpaceMaterial, SpaceTeaching } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import TeachingFields from '@/components/common/TeachingFields.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'

const props = defineProps<{
  /** 这块板今天存着的那份默认；`undefined`（没设过）与 `{}` 一样地填成空格子。 */
  teaching?: SpaceTeaching
  /** 参考资料那一格的候选。取数在容器那边。 */
  materials?: SpaceMaterial[]
  materialsLoading?: boolean
  saving: boolean
}>()

const emit = defineEmits<{
  save: [teaching: SpaceTeaching]
}>()

const { t } = useI18n()

const draft = ref<SpaceTeaching>({})

// 表单跟着这块板走：读回来、换了板、保存后重新读回来，都照它重填一遍。
watch(
  () => props.teaching,
  (teaching) => {
    draft.value = teaching ?? {}
  },
  { immediate: true, deep: true }
)

function submit() {
  // 整份交出去：六格全空 = 清掉这块板的默认，于是没有默认。
  emit('save', draft.value)
}
</script>

<template>
  <SettingsToolbar />

  <form class="settings-card" novalidate @submit.prevent="submit">
    <div class="settings-card__title">{{ t('spaces.guidance.title') }}</div>
    <p class="settings-card__desc">{{ t('spaces.guidance.intro') }}</p>
    <div class="guidance__body">
      <TeachingFields v-model="draft" :materials="props.materials ?? []" :materials-loading="props.materialsLoading" />
    </div>
    <div class="settings-foot">
      <v-btn color="primary" variant="flat" :loading="saving" @click="submit">
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
