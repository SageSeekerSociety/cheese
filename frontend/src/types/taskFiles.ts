import type { TaskRevisionChanges } from '@/lib/taskRevision'

/** 题目的一份材料（题目资料库里的一份文件）。它没有公开地址：字节走受保护的下载端点。 */
export interface TaskFileData {
  /** 名字，也是它的地址。 */
  name: string
  size: number
  /** 放进来的时间（毫秒）。 */
  addedAt: number
}

export type TaskFileListData = {
  files: TaskFileData[]
  /** 起始代码：领题后作为代码仓库第一个提交的那份 .zip；没有是 null。不在 `files` 里。 */
  starter: TaskFileData | null
  /** 能不能下载由服务端说，不从清单在不在推。 */
  canDownload: boolean
}

/** 历史版本里一版手上的一份文件，按版本号和名字取。这份材料后来整份删掉了的，
 *  `size` 是 null，只剩名字。 */
export interface TaskVersionFileData {
  name: string
  size: number | null
}

/** 题目的一版（后端 `routes/tasks/files.py` 的 `TaskVersionOut`）。 */
export interface TaskVersionData {
  number: number
  /** 记下这一版的时刻（毫秒）。 */
  at: number
  author: { id: number; username: string; nickname: string } | null
  changes: TaskRevisionChanges
  /** 这一版手上的文件；记版本时还没记文件的那几版是 null。 */
  files: TaskVersionFileData[] | null
}

export type TaskVersionListData = {
  /** 新的在前。 */
  versions: TaskVersionData[]
  canDownload: boolean
}
