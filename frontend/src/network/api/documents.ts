// 知是 2.0 项目文档 API — 人类前门（REST，`/api` base，`{code,message,data}` 信封）。
// 文档即节点（Notion/飞书式）：任意文档可有子文档，树由后端直接嵌套返回。
// 复用应用统一的 axios 实例（NewApiInstance），与其它人类前门模块一致；
// 不使用 connectorFetch（那是 connector 面板专用）。

import { NewApiInstance } from './index'

// 文档类型（后端 docType 枚举，目前只用 'doc'）。
export type DocType = string

// 树节点：`GET /projects/{id}/documents` 已经嵌套返回 children。
export interface DocumentTreeNode {
  id: number
  projectId: number
  parentId: number | null
  title: string
  docType: DocType
  sortOrder: number
  archived: boolean
  createdBy: number
  createdAt: string
  updatedAt: string
  children: DocumentTreeNode[]
}

// 文档摘要（创建 / 详情里的 document 字段）。
export interface DocumentSummary {
  id: number
  projectId: number
  parentId: number | null
  title: string
  docType: DocType
  sortOrder: number
  archived: boolean
  createdBy: number
  createdAt: string
  updatedAt: string
}

// 详情里的结构块（万物皆块）。前端目前只用整篇 markdown（content），块信息保留以备后续。
export interface DocumentNode {
  id: number
  nodeType: string
  structOrder: number
  content: string
  editedAt: string
}

export interface DocumentDetail {
  document: DocumentSummary
  content: string
  nodes: DocumentNode[]
}

export interface DocumentPatch {
  title?: string
  parentId?: number | null
  sortOrder?: number
  archived?: boolean
}

// 列出项目下的文档树（已嵌套）。
export const listDocuments = (projectId: number) =>
  NewApiInstance.request<{ documents: DocumentTreeNode[] }>({
    url: `/projects/${projectId}/documents`,
    method: 'GET',
  })

// 新建文档；parentId 省略 = 根级。
export const createDocument = (projectId: number, data: { title: string; parentId?: number | null }) =>
  NewApiInstance.request<{ document: DocumentSummary }>({
    url: `/projects/${projectId}/documents`,
    method: 'POST',
    data,
  })

// 获取单篇文档详情（含整篇 markdown content）。
export const getDocument = (documentId: number) =>
  NewApiInstance.request<DocumentDetail>({
    url: `/documents/${documentId}`,
    method: 'GET',
  })

// 保存整篇 markdown 正文。
export const saveDocument = (documentId: number, content: string) =>
  NewApiInstance.request<DocumentDetail>({
    url: `/documents/${documentId}`,
    method: 'PUT',
    data: { content },
  })

// 局部更新元信息（重命名 / 移动 / 排序 / 归档）。移到根传 parentId:null。
export const patchDocument = (documentId: number, patch: DocumentPatch) =>
  NewApiInstance.request<DocumentDetail>({
    url: `/documents/${documentId}`,
    method: 'PATCH',
    data: patch,
  })

// 删除文档（子文档会上提到被删文档的父级）。
export const deleteDocument = (documentId: number) =>
  NewApiInstance.request<null>({
    url: `/documents/${documentId}`,
    method: 'DELETE',
  })
