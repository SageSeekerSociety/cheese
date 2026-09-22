<script setup lang="ts">
import type { AdminCandidate } from '@/api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { searchAdminCandidates } from '@/api'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'

/**
 * AdminAssigneeSelect.vue — 分诊面板里的「指派给谁」。
 *
 * 数据源是 `searchAdminCandidates`（`GET /admin/users?q=`，按 handle 与昵称搜账号、
 * 带 `avatar_id`），**不是**管理员名单 `GET /admin/admins` —— 那张表是「谁能进后台」，
 * 和「这条反馈归谁」是两件事，能分诊的人不必是管理员。用法与 `AdminMembersPage` 的
 * 那个搜索框同源，连防抖与竞态处理都是同一套。
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
  { label: '指派给', dense: false }
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string | null): void }>()

const search = ref('')
const candidates = ref<AdminCandidate[]>([])
const searching = ref(false)

/** 输入框里什么都没有 / 正在搜 / 搜完了没人，是三句不同的话，下拉里只能出现一句。 */
const hint = computed(() => {
  if (searching.value) return '搜索中…'
  return search.value.trim() ? '没找到这个人' : '输入 handle 或昵称'
})

// 250ms 防抖：每敲一个字打一次接口，一次指派会打出十几个请求，而只有最后一个的
// 结果会被看见。与名册页同一个间隔。
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, (q) => {
  if (searchTimer) clearTimeout(searchTimer)
  const wanted = q.trim()
  if (!wanted) {
    // 空串不发请求：接口会把它当成「列前 20 个账号」，那不是一个搜索结果。
    candidates.value = []
    searching.value = false
    return
  }
  searching.value = true
  searchTimer = setTimeout(async () => {
    try {
      const page = await searchAdminCandidates(wanted)
      // 慢的那个请求后到会盖掉新结果，只认当前这串字的答案。
      if (search.value.trim() === wanted) candidates.value = page.items
    } catch {
      // 搜不到人是常态（打到一半、拼音打错），为它弹一个错误气泡反而把面板搞脏；
      // 清空候选，下拉里就会显示「没找到这个人」。
      candidates.value = []
    } finally {
      searching.value = false
    }
  }, 250)
})

// 面板会被关掉：关掉之后那次待发的请求没有必要再打出去。
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <v-autocomplete
    v-model:search="search"
    :model-value="props.modelValue"
    autocomplete="off"
    :label="props.label"
    placeholder="输入 handle 或昵称"
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
