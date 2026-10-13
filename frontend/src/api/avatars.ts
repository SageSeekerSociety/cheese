import { axiosRequest } from './axiosHttp'

/** Avatars ride the primary (legacy) API origin; calls expose only the payload. */

/** 上传一张图，换回它的头像 id。 */
export function createAvatar(file: File): Promise<{ avatarId: number }> {
  const data = new FormData()
  data.append('avatar', file)
  return axiosRequest({
    url: '/avatars',
    method: 'POST',
    data,
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 全局默认头像的 id。各环境的种子数据不同，所以得现问（useChosenAvatar）。 */
export function getDefaultAvatarId(): Promise<{ avatarId: number }> {
  return axiosRequest({ url: '/avatars/default/id', method: 'GET' })
}
