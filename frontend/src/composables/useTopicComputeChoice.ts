// 房间这一项：这个话题（或任务）在哪台工作电脑上跑。一个话题一个容器（2026-09-28，
// 推翻结论 60）：改它就是整个房间一起搬，房间里的每个 AI 队友都换过去。
//
// 拆自 `components/TopicComputePicker.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），发请求落在这里，由调用它的那一层接线 —— 和
// `useProjectComputeSettings.ts` 一个路子（`.claude/rules/architecture.md`「组件不取数」）。
//
// 三种回执，组件那一侧靠 props 分开画：
//   - 存成了：`savedVersion` 涨一版（组件据此收起菜单、清掉展开的表单），并叫一声
//     `onChanged`，让父层把手上这份 profile 重取一次 —— 换到哪台、谁跟着搬，只有读回来
//     才准。
//   - 变成一条提议：一次点击没有改掉任何东西，等人点头（接口照样 200，`proposal`
//     非空是唯一能看出来的地方）。这不是错误，也不叫 `onChanged` —— 房间没变，重取没
//     有东西可换。
//   - 被拒了：原话留给 `error`；菜单不收，让他看着那句话再挑一次。
//
// `saving` 为真时再点一次直接丢掉：同一次选择重复发没有意义，控件那一侧也是灰的
// （这里再挡一道，免得两次点击挤在同一帧里都发出去）。
import type { ComputeChoice } from '@/types/compute'

import { ref } from 'vue'

import { setTopicComputeChoice } from '@/api'
import { t } from '@/i18n'

export interface UseTopicComputeChoiceOptions {
  /** 存成一次（没有变成提议）之后叫一声：换到哪台、谁跟着搬，重取一次 profile 才知道。 */
  onChanged?: () => void
}

export function useTopicComputeChoice(topicId: () => string, options: UseTopicComputeChoiceOptions = {}) {
  const saving = ref(false)
  const error = ref('')
  // 选择变成了一条提议：这次点击没有改掉任何东西，等人点头。不是错误，所以不走
  // `error` 那一行红字，组件把它画在别处。
  const proposal = ref('')
  // 成功回执：每次真的存成（不是提议）就涨一版，组件据此收起菜单、清掉展开的表单。
  const savedVersion = ref(0)
  let generation = 0

  async function select(choice: ComputeChoice, visibility?: 'host' | 'isolated') {
    if (saving.value) return
    const current = ++generation
    saving.value = true
    error.value = ''
    proposal.value = ''
    try {
      const saved = await setTopicComputeChoice(topicId(), choice, visibility ? { visibility } : {})
      if (current !== generation) return
      // 变提议时房间这一项没有变 —— 不说话就等于这次点击石沉大海。回执不发，让组件
      // 留着菜单，那句话就在他刚按下的那个控件上。
      if (saved.proposal) {
        proposal.value = saved.proposal.content
        return
      }
      savedVersion.value += 1
      options.onChanged?.()
    } catch (e) {
      if (current === generation) error.value = e instanceof Error ? e.message : t('work.roomMachine.saveFailed')
    } finally {
      if (current === generation) saving.value = false
    }
  }

  function reset() {
    generation += 1
    saving.value = false
    error.value = ''
    proposal.value = ''
    savedVersion.value = 0
  }

  return { saving, error, proposal, savedVersion, select, reset }
}
