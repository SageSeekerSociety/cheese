import { axiosRequest } from './axiosHttp'

/** Avatars ride the primary (legacy) API origin; calls expose only the payload. */

/**
 * 上传一张图，换回它的头像 id。60 秒和其它上传（attachments/materials/spaces）一致：
 * 后端送审要走内容安全，冷启动那次能顶到 30 秒以上，默认的 30 秒会在服务端还在等
 * 的时候先把请求掐掉——用户看到的是「创建失败」，其实后端可能会成功。
 */
export function createAvatar(file: File): Promise<{ avatarId: number }> {
  const data = new FormData()
  data.append('avatar', file)
  return axiosRequest({
    url: '/avatars',
    method: 'POST',
    data,
    timeout: 60_000,
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 全局默认头像的 id。各环境的种子数据不同，所以得现问（useChosenAvatar）。 */
export function getDefaultAvatarId(): Promise<{ avatarId: number }> {
  return axiosRequest({ url: '/avatars/default/id', method: 'GET' })
}
