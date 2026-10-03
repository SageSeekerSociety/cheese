// 一块板资料库的清单（`GET /spaces/{id}/materials`）：给「参考资料」那两处表单取候选。
//
// 两处（空间设置里的这块板默认、发题页里的单题覆盖）问的是同一个问题、打的是同一个
// 接口，只是板不同 —— 所以取数只有一份。
//
// 这里最要紧的一件事是**把「读不出来」和「一份都没有」分开**。选择器拿「清单里没有」
// 判一个编号失效，所以一旦把一次网络失败当成空清单，它就会把指导里已经引用的编号
// 全判成失效，催着人删掉有效的引用。失败时清单原样留着、只把状态写成 `error`，摆
// 什么由用的人决定。
import type { SpaceMaterial, SpaceMaterialsState } from '@/types'

import { type MaybeRefOrGetter, ref, toValue, watch } from 'vue'

import { SpacesApi } from '@/network/api/spaces'

export function useSpaceMaterials(spaceId: MaybeRefOrGetter<number | undefined>) {
  const materials = ref<SpaceMaterial[]>([])
  const state = ref<SpaceMaterialsState>('loading')

  async function load() {
    const id = toValue(spaceId)
    if (!id || !Number.isFinite(id) || id <= 0) {
      // 还没有一块板可问（地址里没有，或者这块板还没装好）：维持「在读」。这会儿说
      // 「一份都没有」是替一块还不认识的板下结论。
      state.value = 'loading'
      return
    }
    state.value = 'loading'
    try {
      const res = await SpacesApi.listMaterials(id)
      // 期间换了块板（或者页面被切走）就不要再落下来 —— 落下来的会是别人的清单。
      if (toValue(spaceId) !== id) return
      materials.value = res.data.materials ?? []
      state.value = 'ready'
    } catch {
      if (toValue(spaceId) !== id) return
      state.value = 'error'
    }
  }

  watch(
    () => toValue(spaceId),
    () => void load(),
    { immediate: true }
  )

  return { materials, state, load }
}
