import { axiosRequest } from './axiosHttp'

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
