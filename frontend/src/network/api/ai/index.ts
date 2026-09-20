import type { QuotaInfo } from './types'

import { NewApiInstance } from '../index'

export namespace AIApi {
  export const getQuota = () =>
    NewApiInstance.request<{ quota: QuotaInfo }>({
      url: '/ai/quota',
      method: 'GET',
    })
}
