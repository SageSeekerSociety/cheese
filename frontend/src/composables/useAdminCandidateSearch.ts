// 「指派给谁」那个搜索框的取数：250ms 防抖、空串不发请求、慢请求后到不盖新结果、
// 关面板时把待发的请求掐掉，都落在这里；`components/admin/AdminAssigneeSelect.vue`
// 只画候选与提示——组件不吃 API 层（.claude/rules/architecture.md）。
//
// 数据源是 `searchAdminCandidates`（`GET /admin/users?q=`，按 handle 与昵称搜账号、
// 带 `avatar_id`），**不是**管理员名单 `GET /admin/admins` —— 那张表是「谁能进后台」，
// 和「这条反馈归谁」是两件事，能分诊的人不必是管理员。用法与 `AdminMembersPage` 的
// 那个搜索框同源，连防抖与竞态处理都是同一套。
import type { AdminCandidate } from '@/api'

import { onBeforeUnmount, ref, watch } from 'vue'

import { searchAdminCandidates } from '@/api'

/** 与名册页同一个间隔：每敲一个字打一次接口，一次指派会打出十几个请求。 */
const DEBOUNCE_MS = 250

export function useAdminCandidateSearch() {
  /** 输入框里的那串字。挂在 `v-model:search` 上。 */
  const search = ref('')
  const candidates = ref<AdminCandidate[]>([])
  const searching = ref(false)

  let timer: ReturnType<typeof setTimeout> | null = null

  watch(search, (q) => {
    if (timer) clearTimeout(timer)
    const wanted = q.trim()
    if (!wanted) {
      // 空串不发请求：接口会把它当成「列前 20 个账号」，那不是一个搜索结果。
      candidates.value = []
      searching.value = false
      return
    }
    searching.value = true
    timer = setTimeout(async () => {
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
    }, DEBOUNCE_MS)
  })

  // 面板会被关掉：关掉之后那次待发的请求没有必要再打出去。
  onBeforeUnmount(() => {
    if (timer) clearTimeout(timer)
  })

  return { search, candidates, searching }
}
