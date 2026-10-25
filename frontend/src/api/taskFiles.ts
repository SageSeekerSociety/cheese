// 题目的材料：题目资料库（后端 `routes/tasks/files.py`）里出题人放进来的文件。
//
// 字节不经过后端上传，和资料库是同一条分段上传的路（`./partUpload`）：按段直接放进
// 存储，再拿凭据和哈希登记。放进、替换、删掉一份，题目记一版。
import type { TaskFileData, TaskFileListData } from '@/types/taskFiles'

import { axiosRequest } from './axiosHttp'
import { BASE } from './http'
import { sendInParts } from './partUpload'

export type { TaskFileData, TaskFileListData }

const base = (taskId: number) => `/tasks/${taskId}/files`

/** 一道题的材料清单。看得见这道题的人都拿得到，能不能下载在 `canDownload` 里。 */
export function listTaskFiles(taskId: number): Promise<TaskFileListData> {
  return axiosRequest({ url: base(taskId), method: 'GET' }, true)
}

/** 放进一份材料（出题人本人或空间管理员）。撞名不覆盖，返回它最后叫什么。 */
export async function addTaskFile(taskId: number, file: File): Promise<{ name: string; size: number }> {
  const uploaded = await sendInParts(base(taskId), file)
  return axiosRequest({ url: base(taskId), method: 'POST', data: { ...uploaded, filename: file.name } }, true)
}

/** 把一份材料换成新的一版：名字不变，旧的一份留作历史。 */
export async function replaceTaskFile(
  taskId: number,
  name: string,
  file: File
): Promise<{ name: string; size: number }> {
  const uploaded = await sendInParts(base(taskId), file)
  return axiosRequest({ url: base(taskId), method: 'PUT', params: { name }, data: uploaded }, true)
}

/** 删掉一份材料，连同它被替换下来的几版。 */
export function removeTaskFile(taskId: number, name: string): Promise<{ removed: boolean }> {
  return axiosRequest({ url: base(taskId), method: 'DELETE', params: { name } }, true)
}

/** 把一份 .zip 设成起始代码（已有的那份留作历史）。压缩包不合格时服务端说为什么。 */
export async function setTaskStarter(taskId: number, file: File): Promise<{ name: string; size: number }> {
  const uploaded = await sendInParts(base(taskId), file)
  return axiosRequest({ url: `/tasks/${taskId}/starter`, method: 'PUT', data: uploaded }, true)
}

/** 不要起始代码了：之后领题的人拿到的是平台初始化的仓库。 */
export function removeTaskStarter(taskId: number): Promise<{ removed: boolean }> {
  return axiosRequest({ url: `/tasks/${taskId}/starter`, method: 'DELETE' }, true)
}

/** 一份材料的字节。门在服务端：带的是这次请求自己的 Authorization。`downloadFile`
 *  补在末尾的 `download=true` 这里没有对应的参数，后端不看它。 */
export function taskFileRawUrl(taskId: number, name: string): string {
  return `${BASE}${base(taskId)}/raw?name=${encodeURIComponent(name)}`
}
