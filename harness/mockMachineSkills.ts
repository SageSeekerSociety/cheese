import { gate, holdMode, record, sleep } from './recorder'

export async function listMachineSkills() {
  return { skills: [] }
}

export async function fetchMachineSkill(device: string, url: string, _pid: string, kind: string) {
  record('machine', `装到机器 ${device}：${kind} ${url.split('/').pop()}`)
  if (holdMode === 'late') await gate('install')
  else await sleep(1200)
  const name = url.split('/').pop() ?? 'pdf'
  return {
    landed: true,
    plan: [{ name, description: '读 PDF', files: ['SKILL.md'] }],
    skills: [{ name, description: '读 PDF', source_root: `/home/me/.cheese/web-skills/${name}`, digest: 'x', state: 'seen', adopted: false }],
    problems: [],
    roots: [],
    workspaces: [],
  }
}

export async function fetchSkillMarket() {
  return {
    skills: [
      { name: 'pdf', description: '读 PDF、抽表格', url: 'https://github.com/anthropics/skills/tree/main/skills/pdf', source: 'anthropic', kind: 'skill' },
      { name: 'docx', description: '读写 Word 文档', url: 'https://github.com/anthropics/skills/tree/main/skills/docx', source: 'anthropic', kind: 'skill' },
    ],
    plugins: [
      { name: 'reviewer', description: '看代码', url: 'https://github.com/o/r/tree/main/plugins/reviewer', source: 'anthropic', kind: 'plugin' },
    ],
    plugin_total: 1,
    mcp: [],
    notes: [],
  }
}
