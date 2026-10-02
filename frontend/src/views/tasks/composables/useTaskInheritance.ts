// 「建这道题的项目会继承什么」的取数那一半 (#944)。渲染在
// `components/TaskInheritance.vue`（只吃 props），所以取数留在页面这一侧 ——
// 三处要用它：领取确认框、侧栏的「新建项目」创建框、以及侧栏常驻的那一块。
//
// 失败不抛：这一块是**说明**，不是必经之路。取不到就不显示，不拦着人领题、
// 建项目 —— 服务端那条读接口给了 404（题没了、看不见）时也一样。
import type { MaybeRefOrGetter } from 'vue'
import type { TaskInheritanceData } from '@/network/api/tasks/types'

import { ref, toValue, watch } from 'vue'

import { TasksApi } from '@/network/api/tasks'

export function useTaskInheritance(taskId: MaybeRefOrGetter<number | null | undefined>) {
  const inheritance = ref<TaskInheritanceData | null>(null)
  const loading = ref(false)
  // 取过一次就不再取：三个入口共用这份 composable，各建一份实例，但同一道题
  // 的清单不会变 —— 打开对话框时取的那一次就是建项目之前算出来的那一份。
  const loadedFor = ref<number | null>(null)
  // 哪一道题正取着。按 **id** 记，不是按一个布尔：切到另一道题时，在途的那一次
  // 属于上一道题，不该拦住新题的取数（否则屏幕上会一直停着旧题那份清单）。
  const inFlight = ref<number | null>(null)
  // 每次取数一个号。响应回来时对一下号：切过题了，这一份就是旧的，丢掉。
  let seq = 0

  async function load() {
    const id = toValue(taskId)
    if (!id) {
      inheritance.value = null
      loadedFor.value = null
      inFlight.value = null
      return
    }
    if (inFlight.value === id || loadedFor.value === id) return
    // 换了一道题就先把上一道题那份放下：在 B 回来之前摆着 A 的资源包与指导，
    // 比空着更糟 —— 它看着像 B 的。
    if (loadedFor.value !== id) inheritance.value = null
    const mine = ++seq
    inFlight.value = id
    loading.value = true
    try {
      const res = await TasksApi.inheritance(id)
      if (mine !== seq) return
      inheritance.value = res.data
      loadedFor.value = id
    } catch {
      if (mine !== seq) return
      inheritance.value = null
    } finally {
      // 晚到的旧响应不许把新题那一次的「在途」擦掉。
      if (mine === seq) {
        inFlight.value = null
        loading.value = false
      }
    }
  }

  watch(() => toValue(taskId), load, { immediate: true })

  return { inheritance, loading, reload: load }
}
