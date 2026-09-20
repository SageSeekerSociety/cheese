import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'

import { avatarColor, avatarInitial } from '@/utils/avatar'
import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, isChosenAvatar } from './useChosenAvatar'

import { AIApi } from '@/network/api/ai'
import { QuotaInfo } from '@/network/api/ai/types'
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

  // Default-avatar fallback: colored initial derived from the user's identity.
  // Seed on nickname, falling back to the user id so the color is still stable
  // when the nickname is empty.
  const avatarSeed = computed(() => nickname.value || String(AccountService._user.value?.id ?? ''))
  const avatarInitialRef = computed(() => avatarInitial(avatarSeed.value))
  const avatarColorRef = computed(() => avatarColor(avatarSeed.value))

  // 头像要判「这是不是那张全局默认图」，而那一行的 id 因环境而异，得问后端。
  // 那个接口要登录，所以等登录了再问（已登录的话 immediate 当场就问）。
  watch(loggedIn, (yes) => yes && ensureDefaultAvatarId(), { immediate: true })

  // AI 配额状态
  const aiQuota = ref<QuotaInfo | null>(null)

  // 获取 AI 配额
  const fetchAIQuota = async () => {
    if (!loggedIn.value) return
    try {
      const { data } = await AIApi.getQuota()
      aiQuota.value = data.quota
    } catch (error) {
      console.error('Failed to fetch AI quota:', error)
    }
  }

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

  // 当菜单打开时获取 AI 配额
  watch(menuOpen, (newValue) => {
    if (newValue) {
      fetchAIQuota()
    }
  })

  return {
    // 状态
    menuOpen,
    loggedIn,
    currentUser,
    avatar,
    avatarInitial: avatarInitialRef,
    avatarColor: avatarColorRef,
    nickname,
    intro,
    aiQuota,

    // 方法
    fetchAIQuota,
    onLogout,

    // 工具函数
    dayjs,
  }
}
