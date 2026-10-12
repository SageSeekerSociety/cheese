import type { AxiosProgressEvent } from 'axios'
import type { Material } from '@/types'

import { axiosRequest } from './axiosHttp'

/** 一份素材本身。老 origin（primary）；调用方拿到的是 payload，不是整只信封。 */
export function getMaterial(materialId: number): Promise<{ material: Material }> {
  return axiosRequest({ url: `/materials/${materialId}`, method: 'GET' })
}

export function uploadMaterial(
  file: File,
  type: string,
  onProgress?: (progressEvent: AxiosProgressEvent) => void
): Promise<{ id: number }> {
  const data = new FormData()
  data.append('type', type)
  data.append('file', file)
  return axiosRequest({
    url: '/materials',
    method: 'POST',
    data,
    timeout: 60000,
    onUploadProgress: onProgress,
  })
}
