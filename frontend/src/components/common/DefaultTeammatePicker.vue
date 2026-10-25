<script setup lang="ts">
// 「默认队友」这一格（#944）：这道题（或这个项目集）底下建出的项目，芝士会带着哪一位
// 预设队友的技能出生。
//
// 和「参考资料」那一格一样，发题页、改题页、项目集设置共用这一份 —— 只吃 props、
// 只往上发。预设清单由调用方取好传进来（`types` / `typesFailed`），这一格不碰 API 层。
//
// 选择项：第一项是「沿用上层设置」，其余每个平台预设一项，标题用 `title`、
// 副标题用它的技能清单（没有技能就退回 `description`）。预设清单读不到时只留「沿用上层设置」
// 那一项，并在副标题里说一句，把整格藏掉反而让人没法表达「沿用上层设置」。
//
// 提交口径（`''` / 预设名 / 不带键）不在这里判：这一格只管报告人选，怎么落成 payload
// 由调用方按 `defaultRolePayload` 算。
import type { AgentType } from '@/cx_types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    /** 当前提交值：`''` = 这一层不单独指定（继承上层）；或一个预设名。 */
    modelValue?: string | null
    /** 这一层现在写着的原始覆盖（`null` = 本来就没写）。只拿来把「沿用上层设置」那一项讲准。 */
    rawValue?: string | null
    /** 平台预设清单。取数在调用方（页面 / 它用的组合式）—— 这一格只吃 props、
     *  只往上发，才作得成场景里的 A 级画面（docs/manual/dev/scenes.md）。 */
    types?: AgentType[]
    /** 清单没读到时为 true：只留「沿用上层设置」那一项，并在副标题里说一句。 */
    typesFailed?: boolean
    /** 上一层（项目集）设的那一位：预设名；`null` = 上一层也没设；不给 = 不知道上一层是哪个。
     *  知道时「沿用上层设置」写出沿用的是谁，和「方式」那张卡片一样。 */
    inherited?: string | null
  }>(),
  { types: () => [], typesFailed: false, inherited: undefined }
)

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const { t } = useI18n()

/** 沿用的是谁：上一层那一位的名字，上一层没设时说没设；不知道上一层时为空。 */
const inheritedText = computed(() => {
  if (props.inherited === undefined) return ''
  if (props.inherited === null) return t('spaces.teaching.defaultTeammate.inheritNone')
  return props.types.find((type) => type.name === props.inherited)?.title ?? props.inherited
})

const items = computed(() => [
  {
    value: '',
    title: t('spaces.teaching.defaultTeammate.inherit'),
    subtitle: props.typesFailed
      ? t('spaces.teaching.defaultTeammate.loadFailed')
      : inheritedText.value || t('spaces.teaching.defaultTeammate.inheritHint'),
  },
  ...props.types.map((type) => ({
    value: type.name,
    title: type.title,
    subtitle: type.skills?.length ? type.skills.join(' · ') : type.description,
  })),
])

/** 这一层本来就写过、现在又选回「沿用上层设置」：保存会清掉它，说一句。 */
const willClear = computed(() => (props.modelValue ?? '') === '' && props.rawValue != null)
</script>

<template>
  <div class="teammate" data-testid="default-teammate">
    <div class="teammate__label">{{ t('spaces.teaching.defaultTeammate.title') }}</div>
    <p class="teammate__hint">{{ t('spaces.teaching.defaultTeammate.hint') }}</p>
    <v-select
      :model-value="modelValue ?? ''"
      :items="items"
      item-title="title"
      item-value="value"
      variant="outlined"
      density="compact"
      autocomplete="off"
      hide-details
      :aria-label="t('spaces.teaching.defaultTeammate.title')"
      @update:model-value="(value: string) => emit('update:modelValue', value ?? '')"
    >
      <!-- 自带 item 槽就必须自己补 `role="option"`：不补的话 Vuetify 只给默认那一支
           合并这个角色，屏读器认不出这些是选项。 -->
      <template #item="{ item, props: itemProps }">
        <v-list-item v-bind="itemProps" role="option" :subtitle="item.raw?.subtitle" />
      </template>
    </v-select>
    <p
      v-if="(modelValue ?? '') === '' && inheritedText"
      class="teammate__note"
      data-testid="default-teammate-inherited"
    >
      {{ inheritedText }}
    </p>
    <p v-if="willClear" class="teammate__note" data-testid="default-teammate-will-clear">
      {{ t('spaces.teaching.defaultTeammate.willClear') }}
    </p>
  </div>
</template>

<style scoped>
.teammate {
  display: flex;
  flex-direction: column;
}

.teammate__label {
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
}

.teammate__hint {
  margin: 4px 0 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.teammate__note {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
