<script setup lang="ts">
// 「涉及的知识点」这一格（学情分析的底座）：这道题归在这块板的哪几个知识点下。
//
// 摆法是照可点原型 xueqing/demo.html 的 `vEdit()`：采用了的做成 chip 摆在外面（点 chip 上的
// ✕ 去掉），旁边一颗「从清单添加」摊开整份清单；下面才是 AI 读题面给出的建议，一行一条，
// 点「采用」才生效，「忽略」只是眼下不摆它。
//
// 三条规矩落在这里：
//
// - **采用是从两边合起来的判据**：`modelValue` 记的是编号，建议记的是名字。一条建议名叫
//   清单里已有的某条（`isNew` 为假）就直接挂上它的编号；清单里没有的（`isNew`）这一格办不了
//   —— 往清单里加一条要这块板的管理权限，所以它往上发 `adopt-new`，由调用方加进清单再挂上。
//   一条建议的名字一旦进了「已采用」，它自己就从建议里消失了，不用谁再记一遍。
// - **清单读不出来时不判失效**：一次网络失败不该让人丢掉已经挂上的编号，所以 `error` 那一支
//   只写一句「读不到」，不动 `modelValue`，也不摆候选。
// - **清单是这块板共用的**：这一格不建、不改、不删知识点，那些在「设置 › 学情分析」里。
import type { SpaceKnowledgePoint, SpaceKnowledgeSuggestion } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

/** 清单读到哪一步了。`error` 不能当成「没有」——见上。 */
type KnowledgeListState = 'loading' | 'ready' | 'error'

const props = withDefaults(
  defineProps<{
    /** 已经采用的编号。顺序照旧，新挂上的接在后面。 */
    modelValue: number[]
    /** 这块板的知识点清单（所有题目共用）。 */
    knowledgePoints: SpaceKnowledgePoint[]
    /** 清单拿不拿得到。 */
    state?: KnowledgeListState
    /** AI 读题面给出的建议，还没处理的那几条。 */
    suggestions?: SpaceKnowledgeSuggestion[]
    /** 正在读题面。 */
    suggesting?: boolean
    /** 上一次读题面没成。 */
    suggestFailed?: boolean
    /** 能不能让 AI 读题面：与建议那条路由同一道门（这块板的管理权限）。 */
    canSuggest?: boolean
  }>(),
  {
    state: 'ready',
    suggestions: () => [],
    suggesting: false,
    suggestFailed: false,
    canSuggest: false,
  }
)

const emit = defineEmits<{
  'update:modelValue': [number[]]
  suggest: []
  /** 采用一条清单里还没有的知识点：调用方先把它加进清单，再挂上这条题。 */
  'adopt-new': [string]
}>()

const { t } = useI18n()

/** 清单摊开没有。默认收起：多数题目只挂一两条，摊着整份清单天天挡路。 */
const open = ref(false)

/** 人点了「忽略」的那几条（名字）。只是这一屏不摆它，清单和题面都没动。 */
const ignored = ref(new Set<string>())

/** 摆在外面的 chip：照 `modelValue` 的顺序取。清单里没有的编号不摆（软删掉的那些）。 */
const chosen = computed(() =>
  props.modelValue
    .map((id) => props.knowledgePoints.find((point) => point.id === id))
    .filter((point): point is SpaceKnowledgePoint => point !== undefined)
)

/** 已经挂上的名字：一条建议叫这个名字就已经生效了，不用再摆出来问一次。 */
const adopted = computed(() => new Set(chosen.value.map((point) => point.name)))

/** 还等着处理的建议。 */
const pending = computed(() =>
  props.suggestions.filter((item) => !ignored.value.has(item.name) && !adopted.value.has(item.name))
)

/** 建议都处理完了：读题面出过结果，眼下一条待处理的都没有。 */
const allHandled = computed(() => props.suggestions.length > 0 && pending.value.length === 0)

function setIds(ids: number[]) {
  emit('update:modelValue', ids)
}

function toggle(id: number, on: boolean) {
  if (on) {
    if (props.modelValue.includes(id)) return
    setIds([...props.modelValue, id])
    return
  }
  setIds(props.modelValue.filter((current) => current !== id))
}

function adopt(item: SpaceKnowledgeSuggestion) {
  const point = props.knowledgePoints.find((candidate) => candidate.name === item.name)
  if (point) {
    if (!props.modelValue.includes(point.id)) setIds([...props.modelValue, point.id])
    return
  }
  emit('adopt-new', item.name)
}

function ignore(item: SpaceKnowledgeSuggestion) {
  ignored.value = new Set([...ignored.value, item.name])
}

const confidence = (item: SpaceKnowledgeSuggestion) =>
  item.confidence === 'high'
    ? t('spaces.learningAnalysis.field.confidenceHigh')
    : t('spaces.learningAnalysis.field.confidenceMedium')
</script>

<template>
  <div class="kp" data-testid="knowledge-points" data-agent-field="knowledge_points">
    <div class="kp__label">{{ t('spaces.learningAnalysis.field.label') }}</div>
    <p class="kp__hint">{{ t('spaces.learningAnalysis.field.hint') }}</p>

    <p v-if="state === 'error'" class="kp__empty" data-testid="knowledge-points-error">
      {{ t('spaces.learningAnalysis.field.loadFailed') }}
    </p>

    <template v-else>
      <!-- 采用了的摆在这一条里；没挂任何一条时写一句。 -->
      <div class="kp__row" data-testid="knowledge-points-adopted">
        <v-chip
          v-for="point in chosen"
          :key="point.id"
          size="small"
          class="kp__chip"
          data-testid="knowledge-point-chip"
        >
          {{ point.name }}
          <button
            type="button"
            class="kp__chip-x"
            :data-testid="`knowledge-point-chip-remove-${point.id}`"
            :aria-label="t('spaces.learningAnalysis.field.remove', { name: point.name })"
            @click="toggle(point.id, false)"
          >
            ✕
          </button>
        </v-chip>
        <span v-if="chosen.length === 0" class="kp__placeholder" data-testid="knowledge-points-empty">
          {{ t('spaces.learningAnalysis.field.empty') }}
        </span>
        <BaseButton
          kind="ghost"
          size="sm"
          prepend-icon="mdi-plus"
          :disabled="state !== 'ready'"
          data-testid="knowledge-point-add"
          @click="open = !open"
        >
          {{ t('spaces.learningAnalysis.field.addFromList') }}
        </BaseButton>
      </div>

      <div v-if="open && state === 'ready'" class="kp__menu">
        <p v-if="knowledgePoints.length === 0" class="kp__empty" data-testid="knowledge-point-list-empty">
          {{ t('spaces.learningAnalysis.field.noList') }}
        </p>
        <div v-else class="kp__list" data-testid="knowledge-point-list">
          <v-checkbox
            v-for="point in knowledgePoints"
            :key="point.id"
            :model-value="modelValue.includes(point.id)"
            :label="point.name"
            density="compact"
            hide-details
            @update:model-value="(on: unknown) => toggle(point.id, on === true)"
          />
        </div>
      </div>

      <div v-if="canSuggest" class="kp__ai">
        <div class="kp__aihead">
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-auto-fix"
            :loading="suggesting"
            :disabled="suggesting"
            data-testid="knowledge-point-suggest"
            @click="emit('suggest')"
          >
            {{ t('spaces.learningAnalysis.field.suggest') }}
          </BaseButton>
          <span class="kp__aihint">{{ t('spaces.learningAnalysis.field.suggestHint') }}</span>
        </div>

        <p v-if="suggesting" class="kp__empty" data-testid="knowledge-points-suggesting">
          {{ t('spaces.learningAnalysis.field.suggesting') }}
        </p>
        <p v-else-if="suggestFailed" class="kp__empty" data-testid="knowledge-points-suggest-failed">
          {{ t('spaces.learningAnalysis.field.suggestFailed') }}
        </p>

        <div v-if="!suggesting && !suggestFailed" class="kp__suggestions">
          <div v-for="item in pending" :key="item.name" class="kp__suggestion" data-testid="knowledge-point-suggestion">
            <div class="kp__suggestion-main">
              <b class="kp__name">{{ item.name }}</b>
              <span v-if="item.isNew" class="kp__new">{{ t('spaces.learningAnalysis.field.notInList') }}</span>
              <span class="kp__confidence">{{ confidence(item) }}</span>
              <span class="kp__why">{{ item.why }}</span>
            </div>
            <BaseButton kind="primary" size="sm" data-testid="knowledge-point-adopt" @click="adopt(item)">
              {{ item.isNew ? t('spaces.learningAnalysis.field.adoptNew') : t('spaces.learningAnalysis.field.adopt') }}
            </BaseButton>
            <BaseButton kind="ghost" size="sm" data-testid="knowledge-point-ignore" @click="ignore(item)">
              {{ t('spaces.learningAnalysis.field.ignore') }}
            </BaseButton>
          </div>
          <p v-if="allHandled" class="kp__empty" data-testid="knowledge-points-all-handled">
            {{ t('spaces.learningAnalysis.field.allHandled') }}
          </p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.kp {
  display: flex;
  flex-direction: column;
}

.kp__label {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}

.kp__hint {
  margin: 2px 0 0;
  font-size: 0.75rem;
  color: var(--muted);
}

.kp__row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 6px;
}

.kp__chip {
  max-width: 100%;
}

.kp__chip-x {
  margin-left: 6px;
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font-size: 0.75rem;
  line-height: 1;
  cursor: pointer;
}

.kp__placeholder {
  font-size: 0.8125rem;
  color: var(--muted);
}

.kp__menu {
  margin-top: 4px;
  padding: 6px 10px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
}

.kp__list {
  max-height: 220px;
  overflow-y: auto;
}

.kp__empty {
  margin: 8px 0 0;
  font-size: 0.8125rem;
  color: var(--muted);
}

.kp__ai {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--canvas);
}

.kp__aihead {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.kp__aihint {
  font-size: 0.75rem;
  color: var(--muted);
}

.kp__suggestion {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  border-top: 1px solid var(--line);
}

.kp__suggestion:first-child {
  border-top: none;
}

.kp__suggestion-main {
  flex: 1 1 220px;
  min-width: 0;
}

.kp__name {
  color: var(--ink);
  font-weight: 500;
}

.kp__new {
  margin-left: 6px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-size: 0.6875rem;
  color: var(--muted);
}

.kp__confidence {
  margin-left: 6px;
  padding: 0 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  font-size: 0.6875rem;
  color: var(--muted);
}

.kp__why {
  margin-left: 6px;
  font-size: 0.75rem;
  color: var(--muted);
}
</style>
