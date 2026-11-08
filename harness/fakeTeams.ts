// 隔离亲验用的接口替身：场景由页面上的 window.__SCENARIO 决定。不进仓库。
const scenario = (window as any).__SCENARIO as string
const people = [
  { user: { id: 1, nickname: '冯冠瑀', username: 'fengguanyu', avatarId: 0 }, role: 'OWNER', canRemove: false },
  { user: { id: 9, nickname: 'qinmo', username: 'qinmo', avatarId: 0 }, role: 'MEMBER', canRemove: true },
  { user: { id: 10, nickname: 'zhaoyu', username: 'zhaoyu', avatarId: 0 }, role: 'MEMBER', canRemove: true },
]
let reads = 0
;(window as any).__WAITING = []
const never = () => new Promise<never>(() => {})
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
export namespace TeamsApi {
  export type TeamJoinLink = any
  export const getMembers = async (_id: number) => {
    reads += 1
    ;(window as any).__READS = reads
    if (scenario === 'pending') return never()
    if (scenario === 'manual') {
      // 手动放行：每一次读都停在这里，等页面顶上的按钮决定回什么。
      const answer = await new Promise<string>((res) => (window as any).__WAITING.push(res))
      if (answer === 'fail') throw new Error('网络错误（手动放行）')
      return { members: answer === 'empty' ? [] : people }
    }
    await wait(200)
    if (scenario === 'empty') return { members: [] }
    if (scenario === 'failed') throw new Error('网络错误（替身）')
    if (scenario === 'reread' && reads > 1) return never()
    if (scenario === 'reread-fail' && reads > 1) throw new Error('瞬断（替身）')
    return { members: people }
  }
  export const removeMember = async () => true
  export const updateMember = async () => true
  export const listTeamJoinRequests = async () => ({ applications: [] })
  export const listTeamInvitations = async () => ({ invitations: [] })
  export const getJoinLink = async () => ({ token: 'demo', approval: true })
  export const resetJoinLink = async () => ({ token: 'demo2', approval: true })
  export const updateJoinLink = async () => ({ token: 'demo', approval: true })
  export const update = async () => ({ team: {} })
  export const createInvitation = async () => true
  export const approveJoinRequest = async () => true
  export const rejectJoinRequest = async () => true
  export const cancelInvitation = async () => true
}
