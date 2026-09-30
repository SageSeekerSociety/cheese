// What a Cloud work computer can be asked for right now (GET
// /projects/{id}/cloud-supply). `selectable` is what a choice may hold: the
// provider's offering met with the platform's own limits. `provider` is the
// offering as MicroCloud states it. Remaining capacity is not reported, so a
// spec inside the range can still fail to be created.
import { ref } from 'vue'

import { request } from '@/api'

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
export function useCloudSupply(projectId: () => string) {
  const supply = ref<CloudSupply | null>(null)
  const loading = ref(false)
  async function load() {
    if (supply.value || loading.value) return
    loading.value = true
    try {
      supply.value = await getCloudSupply(projectId())
    } catch (e) {
      supply.value = { available: false, reason: e instanceof Error ? e.message : '请求失败' }
    } finally {
      loading.value = false
    }
  }
  return { supply, loading, load }
}
