// 工作方法的形状。组件要吃这个形状又碰不得接口层（`api/projectSkills.ts`），所以放这里。

/** 芝士提议时交代的依据，摆在请人保存的那张卡上。 */
export interface SkillProposal {
  /** 用户为这类事教过它的地方。 */
  taught?: string[]
  /** 这一次怎么算做成了。 */
  accepted?: string
  /** 最接近的已有方法和为什么不并进去。 */
  related?: string
  /** 保存后会删掉的 team 记忆。 */
  absorbs?: string[]
  /** 改一份已有方法时：用户的纠正，或者哪里不对。 */
  reason?: string
}

export interface ProjectSkillContent {
  title: string
  description: string
  inputs: string
  steps: string
  outputs: string
  files: Record<string, string>
}

export interface ProjectSkill extends ProjectSkillContent {
  id: string
  project_id: string
  name: string
  state: 'draft' | 'active'
  shipped_revision: number
  proposed_by: string
  confirmed_by: string | null
  confirmed_at: string | null
  source_topic_id: string | null
  /** 芝士提议的草稿或改动凭什么提的；人写的、已保存的为 null。 */
  proposal: SkillProposal | null
  created_at: string
  updated_at: string
}

export interface ProjectSkillRevision {
  revision: number
  content: ProjectSkillContent
  confirmed_by: string
  note: string
  created_at: string
}
