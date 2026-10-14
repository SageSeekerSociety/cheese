<script setup lang="ts">
import type { AdminCandidate } from '@/types'

import { computed } from 'vue'

import { useAdminCandidateSearch } from '@/composables/useAdminCandidateSearch'

import IdentityView from '@/components/common/IdentityView.vue'
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

/** 一行 / 选中值后面是哪个候选账号。候选是对象，选中值也是（值就是 handle）；当那个
 *  handle 不在当前候选里（换过一次搜索、或当前指派的人没被搜出来）时 `raw` 退成 handle
 *  串，这里给 `null`，界面画纯文字 handle。 */
function candidateOf(item: unknown): AdminCandidate | null {
  const raw = (item as { raw?: unknown } | null)?.raw
  if (raw && typeof raw === 'object' && typeof (raw as AdminCandidate).handle === 'string') {
    return raw as AdminCandidate
  }
  return null
}

/** `IdentityView` 要的四样（同 AdminMembersPageView）：账号数据源没有队友标记，
 *  agent 给 null 让视图按 handle 兜底，不硬说成「人」。 */
function identityOf(item: unknown): { handle: string; name: string; avatarId: number | null; agent: null } | null {
  const c = candidateOf(item)
  return c ? { handle: c.handle, name: c.nickname, avatarId: c.avatar_id, agent: null } : null
}
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
    <!-- Each row is avatar + name (+ @handle) through the shared identity view. Clear
         `title` so Vuetify does not paint the plain text title on top of the face. -->
    <template #item="{ item, props: itemProps }">
      <v-list-item v-bind="itemProps" :title="undefined">
        <IdentityView v-if="identityOf(item)" v-bind="identityOf(item)" :size="30" handle-line />
        <span v-else>{{ item.title }}</span>
      </v-list-item>
    </template>
    <!-- No `#selection` slot here, unlike the plain `v-select`s elsewhere: this is a
         `v-autocomplete`, whose input IS the editable field. A `#selection` slot would
         draw the picked account with a face and drop the handle out of that field — but
         the field must keep showing the handle, because that is what actually gets sent.
         The feedback e2e pins it (`toHaveValue('alice')`) exactly to catch a nickname
         being filed as the handle. The picked person's face still shows on every
         candidate row above (`#item`). -->
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
