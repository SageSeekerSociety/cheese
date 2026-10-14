// 一台机器上扫到的技能（`~/.claude/skills/<name>/SKILL.md` 这类 agentskills.io 目录）。
//
// 正文留在机器原位，平台只记 `source_root` + `digest` 和机器索引（后端 `machine_skill`
// 表）。所以这里只有几件事：读索引、让机器重新扫一遍、登记工作区根和从网上取一份。
//
// 走 `connectorRequest`：这几条挂在 `/connector/*` 下（和 `/connector/my/devices` 同一
// 片区），不带 `/api` 前缀、也不拆 envelope。

import { connectorRequest } from './connector'

/** 扫到的技能在索引里的样子。索引里的技能可以直接勾给队友。 */
export interface MachineSkill {
  name: string
  description: string
  /** 技能目录在机器上的绝对路径。 */
  source_root: string
  /** SKILL.md 的 sha256；机器上改了它就变。 */
  digest: string
  /** seen 只入库｜adopted 已采纳｜changed 采纳后又改了｜missing 采纳过但扫不到了。 */
  state: 'seen' | 'adopted' | 'changed' | 'missing'
  adopted: boolean
}

export interface MachineScan {
  skills: MachineSkill[]
  /** 机器上不合规、没能入库的目录，以及为什么。 */
  problems: string[]
  /** 真去看过的根目录。一个都没有时，空列表就有了解释。 */
  roots: string[]
  /** 人登记的工作区根；每个下面的 `.trae/skills` 也一起扫了。没有就是空。 */
  workspaces: string[]
}

/** 索引，照上一次扫描留下的样子。不碰机器 —— 开个列表不该等一台笔记本。 */
export function listMachineSkills(deviceId: string): Promise<{ skills: MachineSkill[] }> {
  return connectorRequest(`/my/devices/${encodeURIComponent(deviceId)}/skills`)
}

/** `?project_id=` 带上去时，扫描会连**这个项目**的会话 home（`~/.cheese/home/<项目>/
 *  <代号>/.claude/skills`）一起读——队友在会话里写出来的技能就在那儿。 */
function withProject(path: string, projectId?: string | null): string {
  return projectId ? `${path}?project_id=${encodeURIComponent(projectId)}` : path
}

/** 现在就去读那台机器（人点「扫描」）。只入库，不替人采纳。 */
export function scanMachineSkills(deviceId: string, projectId?: string | null): Promise<MachineScan> {
  return connectorRequest(withProject(`/my/devices/${encodeURIComponent(deviceId)}/skills/scan`, projectId), {
    method: 'POST',
  })
}

/** 登记整份工作区根名单（写在那台机器上），写完顺手重扫一遍。 */
export function setMachineSkillRoots(
  deviceId: string,
  roots: string[],
  projectId?: string | null
): Promise<MachineScan> {
  return connectorRequest(withProject(`/my/devices/${encodeURIComponent(deviceId)}/skills/roots`, projectId), {
    method: 'POST',
    body: JSON.stringify({ roots }),
  })
}

/** 落盘前给人看的清单：每颗技能，以及会跟着写进去的文件名（含 SKILL.md）。 */
export interface InstallPlanItem {
  name: string
  description: string
  files: string[]
}

/** 安装并重扫后的结果。 */
export interface WebSkillLanded extends MachineScan {
  landed: true
  plan: InstallPlanItem[]
}

/** 把一份网上的技能取到这台机器上：平台先按 agentskills.io 规范检验，过了才落盘。
 *  `kind: 'plugin'` 时地址是一个插件，它带的每颗技能都会各落一份。
 *  验证通过后直接安装；不再弹出安装确认框。 */
export function fetchMachineSkill(
  deviceId: string,
  url: string,
  projectId?: string | null,
  kind: 'skill' | 'plugin' = 'skill'
): Promise<WebSkillLanded> {
  return connectorRequest(`/my/devices/${encodeURIComponent(deviceId)}/skills/web`, {
    method: 'POST',
    body: JSON.stringify({ url, kind, project_id: projectId ?? null, confirm: true }),
  })
}

/** 市场里的一张卡片。`kind` 说它是哪一类，`url` 是能装它的 GitHub 目录（没有就是空）。 */
export interface SkillMarketCard {
  name: string
  description: string
  url: string
  source: string
  kind: 'skill' | 'plugin' | 'mcp'
}

/** 两家厂商公开的东西，实时读回来的三份清单。某一源倒掉只丢它自己那份。 */
export interface SkillMarket {
  skills: SkillMarketCard[]
  plugins: SkillMarketCard[]
  /** 插件目录一共有多少条；上面只回了前若干张。 */
  plugin_total: number
  mcp: SkillMarketCard[]
  /** 哪个源没读到、为什么。 */
  notes: string[]
}

/** 市场：Anthropic 与 OpenAI 公开的技能 / 插件 / MCP。公开目录，登录就读。 */
export function fetchSkillMarket(): Promise<SkillMarket> {
  return connectorRequest('/skills/market')
}

/** 采纳一项：从此它可以被某个队友勾上；`adopted: false` 是退订，它不再进池子。 */
export function setMachineSkillAdoption(deviceId: string, name: string, adopted: boolean): Promise<MachineSkill> {
  return connectorRequest(`/my/devices/${encodeURIComponent(deviceId)}/skills/${encodeURIComponent(name)}/adopt`, {
    method: 'POST',
    body: JSON.stringify({ adopted }),
  })
}
