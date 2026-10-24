// 题目的材料：题目资料库（后端 `routes/tasks/files.py`）里出题人放进来的文件。
//
// 字节不经过后端上传：先领一个地址，把文件原样 PUT 过去（对象存储的预签名地址，或者以
// `/` 开头的后端自己的地址），再拿领到的凭据登记。放进、替换、删掉一份，题目记一版。
import type { TaskFileData, TaskFileListData } from '@/types/taskFiles'

import { t } from '../i18n'

import { axiosRequest } from './axiosHttp'
import { BASE } from './http'

export type { TaskFileData, TaskFileListData }

const base = (taskId: number) => `/tasks/${taskId}/files`

/** 一道题的材料清单。看得见这道题的人都拿得到，能不能下载在 `canDownload` 里。 */
export function listTaskFiles(taskId: number): Promise<TaskFileListData> {
  return axiosRequest({ url: base(taskId), method: 'GET' }, true)
}

async function sendBytes(taskId: number, file: File): Promise<string> {
  const slot = await axiosRequest<{ url: string; upload: string }>(
    { url: `${base(taskId)}/uploads`, method: 'POST', data: { name: file.name, size: file.size } },
    true
  )
  const put = await fetch(slot.url.startsWith('/') ? `${BASE}${slot.url}` : slot.url, { method: 'PUT', body: file })
  if (!put.ok) throw new Error(t('files.library.uploadFailed', { status: put.status }))
  return slot.upload
}

/** 放进一份材料（出题人本人或空间管理员）。撞名不覆盖，返回它最后叫什么。 */
export async function addTaskFile(taskId: number, file: File): Promise<{ name: string; size: number }> {
  const upload = await sendBytes(taskId, file)
  return axiosRequest({ url: base(taskId), method: 'POST', data: { upload, filename: file.name } }, true)
}

/** 把一份材料换成新的一版：名字不变，旧的一份留作历史。 */
export async function replaceTaskFile(
  taskId: number,
  name: string,
  file: File
): Promise<{ name: string; size: number }> {
  const upload = await sendBytes(taskId, file)
  return axiosRequest({ url: base(taskId), method: 'PUT', params: { name }, data: { upload } }, true)
}

/** 删掉一份材料，连同它被替换下来的几版。 */
export function removeTaskFile(taskId: number, name: string): Promise<{ removed: boolean }> {
  return axiosRequest({ url: base(taskId), method: 'DELETE', params: { name } }, true)
}

/** 一份材料的字节。门在服务端：带的是这次请求自己的 Authorization。`downloadFile`
 *  补在末尾的 `download=true` 这里没有对应的参数，后端不看它。 */
export function taskFileRawUrl(taskId: number, name: string): string {
  return `${BASE}${base(taskId)}/raw?name=${encodeURIComponent(name)}`
}
