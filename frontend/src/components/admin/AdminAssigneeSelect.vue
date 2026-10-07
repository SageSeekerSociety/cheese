<script setup lang="ts">
import { computed } from 'vue'

import { useAdminCandidateSearch } from '@/composables/useAdminCandidateSearch'

import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { t } from '@/i18n'

/**
 * AdminAssigneeSelect.vue — 分诊面板里的「指派给谁」。
 *
 * 搜谁、防抖、竞态都在 `useAdminCandidateSearch` 里（数据源是 `searchAdminCandidates`
 * 而不是管理员名单，理由记在那儿）。
 *
 * `:no-filter="true"` 是这里**必须**写的一行（AdminMembersPage 那里记着这个 bug 的
 * 完整来历）：`v-autocomplete` 默认会拿输入串再筛一次自己的 `items`，筛的是
 * `item-title` —— 也就是 handle。按中文昵称搜出的人，服务端回了，组件当场又筛掉，
 * 表现是「输 handle 搜得到、输中文名搜不到」。
 *
 * 清空 = 取消指派（回 `null`），不是空串：空串在这个领域里没有含义。
 */
defineOptions({ name: 'AdminAssigneeSelect' })

const props = withDefaults(
  defineProps<{
    /** 当前指派人的 handle；`null` 就是没指派。 */
    modelValue: string | null
    label?: string
    /** 分诊面板里那一条窄，用 `compact`；对话框里用默认的 `comfortable`。 */
    dense?: boolean
  }>(),
  { label: undefined, dense: false }
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string | null): void }>()

const { search, candidates, searching } = useAdminCandidateSearch()

/** 输入框里什么都没有 / 正在搜 / 搜完了没人，是三句不同的话，下拉里只能出现一句。 */
const hint = computed(() => {
  if (searching.value) return t('admin.assignee.searching')
  return search.value.trim() ? t('admin.assignee.noMatch') : t('admin.assignee.placeholder')
})
</script>

<template>
  <v-autocomplete
    v-model:search="search"
    :model-value="props.modelValue"
    autocomplete="off"
    :label="props.label ?? t('admin.assignee.label')"
    :placeholder="t('admin.assignee.placeholder')"
    variant="outlined"
    :density="props.dense ? 'compact' : 'comfortable'"
    :items="candidates"
    :loading="searching"
    :no-filter="true"
    item-title="handle"
    item-value="handle"
    hide-details
    clearable
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
      <div class="asel__hint">{{ hint }}</div>
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
