<script setup lang="ts">
// 「参考资料」这一格（#944）：从这块板的资料库里勾，不再手填编号。
//
// 两条规矩落在这里，两处表单（空间设置里的默认、发题页里的覆盖）共用同一份：
//
// - **「仅管理员」那一档不进清单。** 学生和他们的 AI 读不到的东西，不该出现在老师的
//   候选里。服务端按权限给清单（管理员两档都拿得到），过滤只在这一处做，免得两页各
//   写一遍慢慢走样。
// - **已经不在清单里的编号不悄悄丢掉。** 素材删了、或者事后被改成「仅管理员」，指导
//   里那个引用还在（读的时候后端本来就把它过滤掉了），这里把它单列出来让人自己去掉。
//   静默删掉等于替人改一份他没动过的配置 —— 保存的时候他会以为只是换了批课件。
import type { SpaceMaterial } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  /** 现在勾着的编号。顺序照旧，新勾的接在后面 —— 勾一下不该把已有的顺序洗一遍。 */
  modelValue: number[]
  /** 这块板资料库的清单，原样递进来（含「仅管理员」档，过滤在这里做）。 */
  materials: SpaceMaterial[]
  loading?: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [number[]] }>()

const { t } = useI18n()

/** 选得到的：只有「所有成员」那一档。 */
const selectable = computed(() => props.materials.filter((item) => item.visibility === 'members'))

/** 挂着别人的编号：清单里没有的那些（删了、或者被改成「仅管理员」）。 */
const dangling = computed(() => {
  const known = new Set(props.materials.map((item) => item.id))
  return props.modelValue.filter((id) => !known.has(id))
})

function toggle(id: number, on: boolean) {
  if (on) {
    if (props.modelValue.includes(id)) return
    emit('update:modelValue', [...props.modelValue, id])
    return
  }
  emit(
    'update:modelValue',
    props.modelValue.filter((current) => current !== id)
  )
}
</script>

<template>
  <div class="material-picker">
    <div class="material-picker__label">{{ t('spaces.teaching.fields.materials') }}</div>
    <p class="material-picker__hint">{{ t('spaces.teaching.fields.materialsHint') }}</p>

    <p v-if="loading" class="material-picker__empty" data-testid="teaching-materials-loading">
      {{ t('spaces.teaching.fields.materialsLoading') }}
    </p>

    <template v-else>
      <p v-if="selectable.length === 0" class="material-picker__empty" data-testid="teaching-materials-empty">
        {{ t('spaces.teaching.fields.materialsEmpty') }}
      </p>
      <div v-else class="material-picker__list" data-testid="teaching-materials">
        <v-checkbox
          v-for="item in selectable"
          :key="item.id"
          :model-value="modelValue.includes(item.id)"
          :label="item.name"
          density="compact"
          hide-details
          @update:model-value="(on: unknown) => toggle(item.id, on === true)"
        />
      </div>

      <div v-if="dangling.length > 0" class="material-picker__dangling" data-testid="teaching-materials-dangling">
        <p class="material-picker__hint">
          {{ t('spaces.teaching.fields.materialsDangling', { ids: dangling.join(', ') }) }}
        </p>
        <v-btn v-for="id in dangling" :key="id" size="small" variant="text" @click="toggle(id, false)">
          {{ t('spaces.teaching.fields.materialsDanglingDrop', { id }) }}
        </v-btn>
      </div>
    </template>
  </div>
</template>

<style scoped>
.material-picker {
  display: flex;
  flex-direction: column;
}

.material-picker__label {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}

.material-picker__hint {
  margin: 2px 0 0;
  font-size: 0.75rem;
  color: var(--muted);
}

.material-picker__list {
  margin-top: 4px;
  max-height: 220px;
  overflow-y: auto;
}

.material-picker__empty {
  margin: 8px 0 0;
  font-size: 0.8125rem;
  color: var(--muted);
}

.material-picker__dangling {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(var(--v-theme-warning), 0.08);
}
</style>
