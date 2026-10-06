// 「指派给谁」那个下拉**取数的那一半**：按 handle 与昵称搜账号（`GET /admin/users?q=`）、
// 防抖、以及慢请求后到时不覆盖新结果。
//
// 为什么抽出来：这个控件现在要出现在两处 —— 详情自己那一份（`AdminQueueDetailView`
// 只吃 props）和独立可挂的那一份（`AdminAssigneeSelect.vue`）。两边各抄一遍防抖与竞态
// 处理，迟早会漂成两种手感。用法与 `AdminMembersPage` 那个搜索框同源（同一个接口、
// 同一个 250ms），那边记着 `no-filter` 那个 bug 的完整来历。
import type { AdminCandidate } from '@/types/admin'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { searchAdminCandidates } from '@/api'
import { t } from '@/i18n'

/** 250ms 防抖：每敲一个字打一次接口，一次指派会打出十几个请求，而只有最后一个的结果
 *  会被看见。与名册页同一个间隔。 */
const DEBOUNCE_MS = 250

export function useAdminAssigneeSearch() {
  const search = ref('')
  const candidates = ref<AdminCandidate[]>([])
  const searching = ref(false)

  /** 输入框里什么都没有 / 正在搜 / 搜完了没人，是三句不同的话，下拉里只能出现一句。 */
  const hint = computed(() => {
    if (searching.value) return t('admin.assignee.searching')
    return search.value.trim() ? t('admin.assignee.noMatch') : t('admin.assignee.placeholder')
  })

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
    }, DEBOUNCE_MS)
  })

  // 面板会被关掉：关掉之后那次待发的请求没有必要再打出去。
  onBeforeUnmount(() => {
    if (searchTimer) clearTimeout(searchTimer)
  })

  return { search, candidates, searching, hint }
}
