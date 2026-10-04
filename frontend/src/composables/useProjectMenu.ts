// 右键一个项目（rail 上那一格、团队页上那张卡片）：复制链接、打开项目设置，不是所有者
// 的还能退出。都是别处已有的操作——项目菜单、成员页——这里只是把它们挂到那一个项目
// 上，对的是被右键的那个，不一定是正开着的这个。
//
// 退出确认框整个应用只挂一个（App.vue），所以「要退哪个」是模块级的一份状态：从哪一页
// 右键，打开的都是那一个确认框。
import type { Router } from 'vue-router'
import type { MenuAction } from '@/components/common/menuAction'
import type { Project } from '@/cx_types'

import { ref } from 'vue'

import { copyLink, linkOf } from '@/commands/copy'
import { t } from '@/i18n'
import { myHandle } from '@/me'

const leaveOpen = ref(false)
const leavingProjectId = ref<string | null>(null)

export function useProjectMenu(router: Router) {
  function projectMenu(project: Project): MenuAction[] {
    const actions: MenuAction[] = [
      {
        key: 'project.copyLink',
        label: t('work.room.menu.copyLink'),
        icon: 'mdi-link-variant',
        onSelect: () => void copyLink(linkOf(router, { name: 'workspace-project', params: { projectId: project.id } })),
      },
      {
        key: 'project.settings',
        label: t('work.projectSettings.title'),
        icon: 'mdi-cog-outline',
        onSelect: () => void router.push({ name: 'project-settings', params: { projectId: project.id } }),
      },
    ]
    if (project.owner_handle !== myHandle())
      actions.push({
        key: 'project.leave',
        label: t('work.members.leave'),
        icon: 'mdi-exit-to-app',
        danger: true,
        onSelect: () => {
          leavingProjectId.value = project.id
          leaveOpen.value = true
        },
      })
    return actions
  }
  return { projectMenu, leaveOpen, leavingProjectId }
}
