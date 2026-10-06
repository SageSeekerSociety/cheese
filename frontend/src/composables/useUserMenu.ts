import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, isChosenAvatar } from './useChosenAvatar'

import { UserApi } from '@/network/api/users'
import AccountService from '@/services/account'

/**
 * 用户菜单相关的公共逻辑
 */
export function useUserMenu() {
  const router = useRouter()

  // 用户相关状态
  const menuOpen = ref(false)
  const loggedIn = computed(() => AccountService._loggedIn.value)
  const currentUser = computed(() => AccountService._user.value)
  // null = 这个人没挑过头像，画彩色首字母（见 useChosenAvatar）。**不要**改回
  // 无条件的 getAvatarUrl：那样返回的永远是个非空 URL，下游每一处「没头像就画
  // 首字母」的兜底分支都变成走不到的死代码，所有没挑过头像的人共用同一张脸。
  const avatar = computed(() => {
    // isChosenAvatar 读的是那个 ref，所以默认头像 id 一到货这里就自动重算。
    const id = AccountService._user.value?.avatarId
    return isChosenAvatar(id) ? getAvatarUrl(id) : null
  })
  const nickname = computed(() => AccountService._user.value?.nickname ?? '')
  const intro = computed(() => AccountService._user.value?.intro ?? '')

  // 这里**不再**导出 avatarInitial / avatarColor 那套兜底：彩色首字母只由 UserAvatar
  // 一处画（契约 §3.14），它自己按 `seed` 取色、按 `name` 取首字母。给用户的颜色种子
  // 用 handle（username），不是昵称——改个昵称不该换一身颜色，而且同一个人在左栏、右
  // 上菜单卡、顶栏三处得是同一个色。调用方把 `avatar` 和 `currentUser.username` 一起
  // 交给 UserAvatar 即可。

  // 头像要判「这是不是那张全局默认图」，而那一行的 id 因环境而异，得问后端。
  // 那个接口要登录，所以等登录了再问（已登录的话 immediate 当场就问）。
  watch(loggedIn, (yes) => yes && ensureDefaultAvatarId(), { immediate: true })

  // 退出登录
  const onLogout = async () => {
    try {
      await UserApi.logout()
    } catch (error) {
      console.warn('Logout request failed; clearing local session anyway:', error)
    } finally {
      AccountService.logout()
      router.push('/')
    }
  }

  return {
    // 状态
    menuOpen,
    loggedIn,
    currentUser,
    avatar,
    nickname,
    intro,

    // 方法
    onLogout,
  }
}
