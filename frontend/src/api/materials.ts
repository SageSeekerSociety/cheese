import type { AxiosProgressEvent } from 'axios'

import { axiosRequest } from './axiosHttp'

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
