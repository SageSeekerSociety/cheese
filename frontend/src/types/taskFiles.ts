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
  /** 能不能下载由服务端说，不从清单在不在推。 */
  canDownload: boolean
}
