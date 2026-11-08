import { gate, holdMode, record, sleep } from './recorder'

import { ApiError } from '@/api/http'

export async function listMyDevices() {
  return {
    devices: [{ device_id: 'dev-mine', name: '我的电脑', online: true, project_ids: [], team_ids: [], screens: [] }],
  }
}

export async function setAgentEditing(_pid: string, agentId: string, on: boolean) {
  if (!on) return { editing: null }
  if (holdMode === 'free') {
    record('lease', `占用 ${agentId}：占到了`)
    return { editing: null }
  }
  if (holdMode === 'late') await gate('lease')
  record('lease', `占用 ${agentId}：alice 正拿着（409）→ 面板只读`)
  throw new ApiError(409, 'alice 正在编辑这个队友')
}

let version = 1
export async function updateProjectAgent(
  _pid: string,
  agentId: string,
  body: { configuration: { skills: string[] }; expected_version: number }
) {
  record('list', `写队友 ${agentId} 的技能名单 → [${body.configuration.skills.join(', ')}]`)
  await sleep(300)
  version += 1
  return { id: agentId, version, configuration: body.configuration }
}
