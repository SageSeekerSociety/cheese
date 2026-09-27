<script setup lang="ts">
import type { TopicNaming, TopicNamingMode } from '../api'

import { onMounted, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'

import { getTopicNaming, setTopicNaming } from '../api'

// 话题命名：平台自动给话题起名、方向变了再改（默认），还是全由人来起名。
// 两档都不碰人定过的名字——那是每个话题自己的锁，在侧栏里「恢复自动命名」解开。
// 行为见后端 topic/naming.py。选中即保存：两个选项、没有要一起提交的别的字段。
const props = defineProps<{ projectId: string }>()

const state = ref<TopicNaming | null>(null)
const error = ref('')
const busy = ref(false)

const OPTIONS: { value: TopicNamingMode; title: string; detail: string }[] = [
  {
    value: 'auto',
    title: '智能命名',
    detail:
      '第一句话后自动起名，第一轮结束后校准一次；之后话题方向明显变了才会改名，改名会在话题里留一条可撤销的记录。',
  },
  {
    value: 'manual',
    title: '手动命名',
    detail: '平台不再自动起名，新话题保持「新话题」，直到有人改名。侧栏的「智能重命名」仍可生成建议，确认后才生效。',
  },
]

async function load() {
  error.value = ''
  try {
    state.value = await getTopicNaming(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载话题命名设置失败'
  }
}

async function choose(mode: TopicNamingMode | null) {
  if (!mode || !state.value?.can_manage || mode === state.value.mode) return
  busy.value = true
  error.value = ''
  try {
    state.value = await setTopicNaming(props.projectId, mode)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存话题命名设置失败'
  } finally {
    busy.value = false
  }
}

const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">人手动改过名字的话题，无论选哪一档都不会被自动改。</p>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <div class="naming-options" role="radiogroup" aria-label="话题命名" data-testid="topic-naming-mode">
        <button
          v-for="o in OPTIONS"
          :key="o.value"
          type="button"
          role="radio"
          class="naming-option"
          :class="{ 'naming-option--on': state.mode === o.value }"
          :aria-checked="state.mode === o.value"
          :disabled="busy || !state.can_manage"
          @click="choose(o.value)"
        >
          <span class="naming-option__dot" aria-hidden="true" />
          <span class="naming-option__text">
            <span class="naming-option__title">{{ o.title }}</span>
            <span class="naming-option__detail c-muted">{{ o.detail }}</span>
          </span>
        </button>
      </div>
      <p v-if="!state.available && state.mode === 'auto'" class="t-caption c-muted mt-2">
        这个部署还没有接模型网关，暂时由 AI 队友在第一轮里起名。
      </p>
      <p v-if="!state.can_manage" class="t-caption c-muted mt-2">只有项目管理员可以修改。</p>
    </template>
  </div>
</template>

<style scoped>
.naming-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 620px;
}

.naming-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  text-align: left;
  transition: border-color 0.15s ease;
}

.naming-option:hover:not(:disabled) {
  border-color: var(--line-2);
}

.naming-option:disabled {
  cursor: default;
}

.naming-option--on {
  border-color: var(--accent);
}

.naming-option__dot {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 4px;
  border: 2px solid var(--line-2);
  border-radius: 999px;
}

.naming-option--on .naming-option__dot {
  border: 4px solid var(--accent);
}

.naming-option__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.naming-option__title {
  font-size: 14px;
  font-weight: 500;
}

.naming-option__detail {
  font-size: 13px;
  line-height: 1.6;
}
</style>
