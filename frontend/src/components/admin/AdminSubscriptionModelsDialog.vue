// 「上架模型」对话框：从这条订阅账号的可用清单里勾选要喂给网关的模型。 // // 模型可用性按账号门控，清单由 codex
后端回答（`available-models`），平台不写死 // —— 写死的代价是账号不支持时轮次 400。勾选状态初始化自服务端的 `shelved` //
标记，保存是**整集替换**：新勾的上架、取消的下架（网关停用）、留任的重推凭据。 // 全部取消并保存 =
全部下架（这条订阅仍在，只是不再喂任何模型）。 // // 失败语义照服务端：503 网关/OpenAI 不可达、502
凭据被判死（去「重新授权」）、 // 409 名字撞了网关在服模型 —— 原话就地显示，对话框不关。
<template>
  <v-dialog :model-value="modelValue" max-width="560" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="px-4 pt-4 asmd__title t-title">{{ t('models.subscription.shelve.title') }}</v-card-title>

      <v-card-text class="px-4">
        <p class="t-meta-read asmd__intro">{{ t('models.subscription.shelve.intro') }}</p>

        <div v-if="loading" class="asmd__loading">
          <v-progress-circular indeterminate size="22" width="2" />
        </div>

        <template v-else>
          <div v-if="items.length" class="asmd__list">
            <label v-for="item in items" :key="item.slug" class="asmd__row">
              <input v-model="checked" type="checkbox" :value="item.slug" class="asmd__check" />
              <span class="asmd__rowtext">
                <span class="asmd__name">{{ item.display_name }}</span>
                <span class="asmd__slug t-num">{{ item.slug }}</span>
                <span v-if="item.description" class="asmd__desc t-meta-read">{{ item.description }}</span>
              </span>
            </label>
          </div>
          <p v-else class="t-meta-read">{{ t('models.subscription.shelve.empty') }}</p>
        </template>

        <v-alert v-if="errorText" type="error" density="compact" variant="tonal" role="alert" class="mt-3">
          {{ errorText }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">{{ t('models.dialog.cancel') }}</v-btn>
        <v-btn color="primary" :loading="saving" :disabled="loading" @click="save">
          {{ t('models.subscription.shelve.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getSubscriptionAvailableModels, putSubscriptionModels, type SubscriptionAvailableModel } from '@/api'

const props = defineProps<{
  modelValue: boolean
  /** 管哪条订阅的上架集合。 */
  subscriptionId: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  /** 保存成功：页面该重拉列表了（上架的模型这才出现在选择器里）。 */
  (e: 'saved'): void
}>()

const { t } = useI18n()

const items = ref<SubscriptionAvailableModel[]>([])
const checked = ref<string[]>([])
const loading = ref(false)
const saving = ref(false)
const errorText = ref<string | null>(null)

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    loading.value = true
    errorText.value = null
    try {
      const res = await getSubscriptionAvailableModels(props.subscriptionId)
      items.value = res.items
      checked.value = res.items.filter((item) => item.shelved).map((item) => item.slug)
    } catch (e) {
      items.value = []
      errorText.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  },
  // 打开那一刻就在 true 上（父级 v-if 挂载），没有「变化」可等 —— 立即跑。
  { immediate: true }
)

async function save() {
  saving.value = true
  errorText.value = null
  try {
    const selected = new Set(checked.value)
    await putSubscriptionModels(
      props.subscriptionId,
      items.value
        .filter((item) => selected.has(item.slug))
        .map((item) => ({ upstream_model: item.slug, label: item.display_name }))
    )
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    errorText.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.asmd__intro {
  margin: 0 0 12px;
}

.asmd__loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.asmd__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
}

.asmd__row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.asmd__row:hover {
  background: rgb(var(--v-theme-on-surface), 0.04);
}

.asmd__check {
  margin-top: 3px;
}

.asmd__rowtext {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.asmd__name {
  font-weight: 500;
}

.asmd__slug {
  font-size: 12px;
  opacity: 0.65;
}
</style>
