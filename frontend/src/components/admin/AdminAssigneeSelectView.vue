<script setup lang="ts">
import type { AdminCandidate } from '@/api'

import { useI18n } from 'vue-i18n'

import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'

/**
 * AdminAssigneeSelectView.vue — 「指派给谁」那个下拉**画的那一半**。
 *
 * 取数（搜账号、防抖、竞态）在 `composables/useAdminAssigneeSearch.ts`，由调用方的容器
 * 跑完当 props 递下来。这一件只吃 props、只往上发事件，所以它能跟着详情一起被单独挂起来看。
 *
 * `:no-filter="true"` 是这里**必须**写的一行（来历见 `AdminAssigneeSelect.vue`）：
 * `v-autocomplete` 默认会拿输入串再筛一次自己的 `items`，筛的是 `item-title` —— 也就是
 * handle。按中文昵称搜出的人，服务端回了，组件当场又筛掉，表现是「输 handle 搜得到、
 * 输中文名搜不到」。
 *
 * 清空 = 取消指派（回 `null`），不是空串：空串在这个领域里没有含义。
 */
defineOptions({ name: 'AdminAssigneeSelectView' })

const props = withDefaults(
  defineProps<{
    /** 当前指派人的 handle；`null` 就是没指派。 */
    modelValue: string | null
    label?: string
    /** 分诊面板里那一条窄，用 `compact`；对话框里用默认的 `comfortable`。 */
    dense?: boolean
    /** 输入框里正在打的字。搜的是服务端，这个字只是受控值。 */
    search: string
    items: AdminCandidate[]
    loading: boolean
    /** 下拉里那一句提示（没有输入 / 正在搜 / 搜完了没人）。 */
    hint: string
  }>(),
  { label: undefined, dense: false }
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string | null): void
  (e: 'update:search', v: string): void
}>()

const { t } = useI18n()
</script>

<template>
  <v-autocomplete
    :search="props.search"
    :model-value="props.modelValue"
    autocomplete="off"
    :label="props.label ?? t('admin.assignee.label')"
    :placeholder="t('admin.assignee.placeholder')"
    variant="outlined"
    :density="props.dense ? 'compact' : 'comfortable'"
    :items="props.items"
    :loading="props.loading"
    :no-filter="true"
    item-title="handle"
    item-value="handle"
    hide-details
    clearable
    @update:search="emit('update:search', $event)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <!-- 昵称当标题、handle 当副标题：搜的是账号，但人认的是昵称。 -->
    <template #item="{ item, props: itemProps }">
      <v-list-item v-bind="itemProps">
        <template #prepend>
          <FeedbackAuthorAvatar :handle="item.raw.handle" :avatar-id="item.raw.avatar_id" :size="30" />
        </template>
        <v-list-item-title>{{ item.raw.nickname }}</v-list-item-title>
        <v-list-item-subtitle>{{ item.raw.handle }}</v-list-item-subtitle>
      </v-list-item>
    </template>
    <!-- 这一句得挂在 `no-data` 上，不能挂 `append-item`：`hide-no-data` 会让候选为空时
         整张菜单直接不可打开（VSelect 的 `menuDisabled`），那时什么提示都露不出来。 -->
    <template #no-data>
      <div class="asel__hint">{{ props.hint }}</div>
    </template>
  </v-autocomplete>
</template>

<style scoped>
/* 对齐 `v-list-item` 自己的左右留白，读起来像列表里的一行而不是一块孤零零的文字。 */
.asel__hint {
  padding: 8px 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
