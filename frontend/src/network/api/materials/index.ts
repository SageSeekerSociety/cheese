import { AxiosProgressEvent } from 'axios'

import ApiInstance from '../index'

export namespace MaterialsApi {
  // 老栈仅存的一支：合同测试还拿它跟新栈 uploadMaterial 对拍（旧整只信封 vs 新 payload），
  // 老层整体退役前别再加新调用方。
  export const upload = (file: File, type: string, onProgress?: (progressEvent: AxiosProgressEvent) => void) => {
    const formData = new FormData()
    formData.append('type', type)
    formData.append('file', file)

    return ApiInstance.request<{ id: number }>({
      url: '/materials',
      method: 'POST',
      data: formData,
      timeout: 60000,
      onUploadProgress: onProgress,
      // headers: {
      //   'Content-Type': 'multipart/form-data',
      // },
    })
  }
}
