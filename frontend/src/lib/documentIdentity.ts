// 一份文档的身份：哪个房间、哪条路径、哪个来源（某个任务的工作树，还是房间自己的
// 文件）、哪个版本。判断「眼前这份字节还是不是刚才那一份」用它——预览看的是芝士交付
// 的那一份，改动看的是某个任务分支上的那一份，两边判据一样。
//
// 它和取字节那一层（lib/documentBytes.ts）分家，只因为这里一次接口都不调。预览里几个
// 只做「还是不是同一份」的钩子（页码钉子、图上圈一块、引用一段）要的就是这个判断，
// 却不必为了它把自己的测试和演示环境搭成「先立一个假后端」。
import type { FileSource } from '../cx_types'

export interface DocumentIdentity {
  topicId: string
  path: string
  taskId: string | null
  source: FileSource
  version: string | null
}

export interface DocumentSnapshot {
  bytes: ArrayBuffer
  identity: Readonly<DocumentIdentity>
  sourceVersion: string | null
}

export function sameDocumentIdentity(left: DocumentIdentity, right: DocumentIdentity): boolean {
  return (
    left.topicId === right.topicId &&
    left.path === right.path &&
    left.taskId === right.taskId &&
    left.source === right.source &&
    left.version === right.version
  )
}
