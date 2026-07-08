import type { Page, Project, ProjectMember } from '@/types'

import { NewApiInstance } from '../index'

export namespace ProjectsApi {
  export type CreateProjectRequestData = {
    name: string
    description: string
    colorCode: string
    startDate: number
    endDate: number
    teamId: number
    leaderId: number
    parentId?: number
    externalTaskId?: number
    githubRepo?: string
    memberIds?: number[]
    externalCollaboratorIds?: number[]
  }

  export type GetProjectsRequestParams = {
    team_id: number
    parent_id?: number
    leader_id?: number
    member_id?: number
    pageStart?: number
    pageSize?: number
  }

  export const create = (data: CreateProjectRequestData) =>
    NewApiInstance.request<{ project: Project }>({
      url: '/projects',
      method: 'POST',
      data,
    })

  export const list = (params: GetProjectsRequestParams) =>
    NewApiInstance.request<{ projects: Project[] }>({
      url: '/projects',
      method: 'GET',
      params,
    })

  // 知是 2.0：当前用户的项目（含无小队的个人项目）。这是「我的项目」的唯一真源。
  export const listMine = () =>
    NewApiInstance.request<{ projects: Project[]; total: number }>({
      url: '/projects/mine',
      method: 'GET',
    })

  // 知是 2.0：创建个人项目（无需小队）。返回同 detail 的 project 模型。
  export const createMine = (data: { name: string; description?: string }) =>
    NewApiInstance.request<{ project: Project }>({
      url: '/projects/mine',
      method: 'POST',
      data,
    })

  // 知是 2.0：重命名 / 改简介（PATCH）。项目 OWNER/leader 才有权限。
  export const renameProject = (projectId: number, data: { name: string; description?: string }) =>
    NewApiInstance.request<{ project: Project }>({
      url: `/projects/${projectId}`,
      method: 'PATCH',
      data,
    })

  // 知是 2.0：删除项目（软删除，DELETE）。项目 OWNER/leader 才有权限。
  export const deleteProject = (projectId: number) =>
    NewApiInstance.request({
      url: `/projects/${projectId}`,
      method: 'DELETE',
    })

  export const detail = (projectId: number) =>
    NewApiInstance.request<{ project: Project }>({
      url: `/projects/${projectId}`,
      method: 'GET',
    })

  export const update = (projectId: number, data: Partial<CreateProjectRequestData> & { archived?: boolean }) =>
    NewApiInstance.request<{ project: Project }>({
      url: `/projects/${projectId}`,
      method: 'PATCH',
      data,
    })

  export const del = (projectId: number) =>
    NewApiInstance.request({
      url: `/projects/${projectId}`,
      method: 'DELETE',
    })

  export const addMember = (projectId: number, userId: number, role?: string) =>
    NewApiInstance.request<{ project: Project }>({
      url: `/projects/${projectId}/members`,
      method: 'POST',
      data: { userId, role },
    })

  export const removeMember = (projectId: number, userId: number) =>
    NewApiInstance.request({
      url: `/projects/${projectId}/members/${userId}`,
      method: 'DELETE',
    })

  export const getMembers = (projectId: number) =>
    NewApiInstance.request<{ members: ProjectMember[] }>({
      url: `/projects/${projectId}/members`,
      method: 'GET',
    })
}
