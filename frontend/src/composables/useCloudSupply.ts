// What a Cloud work computer can be asked for right now (GET
// /projects/{id}/cloud-supply). `selectable` is what a choice may hold: the
// provider's offering met with the platform's own limits. `provider` is the
// offering as MicroCloud states it. Remaining capacity is not reported, so a
// spec inside the range can still fail to be created.
import { ref } from 'vue'

import { request } from '@/api'
import { t } from '@/i18n'

export interface SupplyBound {
  min: number
  max: number
}
export interface CloudSupplyRanges {
  cores: SupplyBound | null
  memory_mb: SupplyBound | null
  disk_gb: SupplyBound | null
}
export type CloudSupply =
  | {
      available: true
      offering: string
      selectable: CloudSupplyRanges
      provider: CloudSupplyRanges
      capacity_known: boolean
    }
  | { available: false; reason: string }

export function getCloudSupply(projectId: string): Promise<CloudSupply> {
  return request<CloudSupply>(`/projects/${encodeURIComponent(projectId)}/cloud-supply`)
}

// 问一次、记住结果；问不到就把原因当作结果，不拿平台自己的上下限冒充。
// 记住只是这一次显示用的，不是长期缓存：`load` 每次都真的问一遍（只有在途的
// 那次会被合并成一个）。云端供应会变、查询也会失败，把上一次的答案一直留着，
// 合法的配置会被旧数一直禁着，恢复了的服务也一直显示查不到。
export function useCloudSupply(projectId: () => string) {
  const supply = ref<CloudSupply | null>(null)
  const loading = ref(false)
  let inflight: Promise<void> | null = null
  function load(): Promise<void> {
    if (inflight) return inflight
    loading.value = true
    inflight = (async () => {
      try {
        supply.value = await getCloudSupply(projectId())
      } catch (e) {
        supply.value = {
          available: false,
          reason: e instanceof Error ? e.message : t('work.cloudSupply.requestFailed'),
        }
      } finally {
        loading.value = false
        inflight = null
      }
    })()
    return inflight
  }
  return { supply, loading, load }
}
