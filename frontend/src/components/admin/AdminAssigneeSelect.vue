<script setup lang="ts">
import { useAdminAssigneeSearch } from '@/composables/useAdminAssigneeSearch'

import AdminAssigneeSelectView from '@/components/admin/AdminAssigneeSelectView.vue'

/**
 * AdminAssigneeSelect.vue — 分诊面板里的「指派给谁」，**取数的那一半**。
 *
 * 数据源是 `searchAdminCandidates`（`GET /admin/users?q=`，按 handle 与昵称搜账号、
 * 带 `avatar_id`），**不是**管理员名单 `GET /admin/admins` —— 那张表是「谁能进后台」，
 * 和「这条反馈归谁」是两件事，能分诊的人不必是管理员。搜账号、防抖与竞态处理都在
 * `composables/useAdminAssigneeSearch.ts` 里，画面在 `AdminAssigneeSelectView.vue` 里，
 * 于是详情那半可以在不取数的情况下单独挂起来看。
 *
 * `:no-filter="true"` 的理由写在 `AdminAssigneeSelectView.vue`（AdminMembersPage 那里
 * 记着这个 bug 的完整来历）：`v-autocomplete` 默认会拿输入串再筛一次自己的 `items`，
 * 筛的是 `item-title` —— 也就是 handle。按中文昵称搜出的人，服务端回了，组件当场又筛掉，
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

const { search, candidates, searching, hint } = useAdminAssigneeSearch()

/** 输入框里那个字往上报时改的是这一层存的引用。 */
function setSearch(v: string) {
  search.value = v
}
</script>

<template>
  <AdminAssigneeSelectView
    :model-value="props.modelValue"
    :label="props.label"
    :dense="props.dense"
    :search="search"
    :items="candidates"
    :loading="searching"
    :hint="hint"
    @update:search="setSearch"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
