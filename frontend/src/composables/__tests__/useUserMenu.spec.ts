// 右上角（手机）/ 左栏底部（桌面）那个「我」的头像。
//
// 注册的每条路径都把档案的 avatar_id 写死成全局默认头像，所以「有 avatar_id」
// 从来不等于「挑过头像」。照原样去取图，每个没挑过的人都是同一张脸，而下面那句
// 「没头像就画彩色首字母」变成永远走不到的死代码。
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getDefaultAvatarId = vi.fn()
const user = ref<{ id: number; nickname: string; avatarId: number | null } | null>(null)
const loggedIn = ref(true)

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/network/api/avatars', () => ({ AvatarsApi: { getDefaultAvatarId: () => getDefaultAvatarId() } }))
vi.mock('@/network/api/ai', () => ({ AIApi: { getQuota: vi.fn() } }))
vi.mock('@/network/api/users', () => ({ UserApi: { logout: vi.fn() } }))
vi.mock('@/services/account', () => ({ default: { _user: user, _loggedIn: loggedIn, logout: vi.fn() } }))

/** 默认头像那一行的 id 是种子数据，各环境不同 —— 所以要现问。 */
const DEFAULT_AVATAR_ID = 1

const settle = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 每个用例都要一份干净的模块状态：默认头像 id 是进程内共享的一次性缓存。 */
async function freshUserMenu() {
  vi.resetModules()
  const { useUserMenu } = await import('../useUserMenu')
  const menu = useUserMenu()
  await settle()
  return menu
}

beforeEach(() => {
  loggedIn.value = true
  getDefaultAvatarId.mockResolvedValue({ data: { avatarId: DEFAULT_AVATAR_ID } })
})

describe('我的头像', () => {
  it('从来没挑过头像 → 不给图，让界面画彩色首字母', async () => {
    user.value = { id: 7, nickname: '爱丽丝', avatarId: DEFAULT_AVATAR_ID }
    const menu = await freshUserMenu()
    expect(menu.avatar.value).toBeNull()
    expect(menu.avatarInitial.value).toBe('爱')
    expect(menu.avatarColor.value).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('自己挑过头像 → 就画那张', async () => {
    user.value = { id: 7, nickname: '爱丽丝', avatarId: 4242 }
    const menu = await freshUserMenu()
    expect(menu.avatar.value).toContain('/avatars/4242')
  })

  it('两个人的兜底颜色不一样 —— 认人正是头像的全部职责', async () => {
    user.value = { id: 7, nickname: '爱丽丝', avatarId: DEFAULT_AVATAR_ID }
    const alice = (await freshUserMenu()).avatarColor.value
    user.value = { id: 8, nickname: '鲍勃', avatarId: DEFAULT_AVATAR_ID }
    const bob = (await freshUserMenu()).avatarColor.value
    expect(alice).not.toBe(bob)
  })

  it('问不到哪一行是默认头像时，照旧取图 —— 宁可多显示一张，也不能把真头像藏了', async () => {
    getDefaultAvatarId.mockRejectedValue(new Error('offline'))
    user.value = { id: 7, nickname: '爱丽丝', avatarId: 4242 }
    const menu = await freshUserMenu()
    expect(menu.avatar.value).toContain('/avatars/4242')
  })

  it('压根没有 avatarId（还没登进来）→ 不给图', async () => {
    user.value = null
    const menu = await freshUserMenu()
    expect(menu.avatar.value).toBeNull()
  })
})
