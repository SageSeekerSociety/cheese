// 技能的形状。组件要吃这个形状又碰不得接口层（`api/projectSkills.ts`），所以放这里。

/** 芝士提议时交代的依据，摆在请人保存的那张卡上。 */
export interface SkillProposal {
  /** 用户为这类事教过它的地方。 */
  taught?: string[]
  /** 这一次怎么算做成了。 */
  accepted?: string
  /** 最接近的已有方法和为什么不并进去。 */
  related?: string
  /** 保存后会删掉的 team 记忆。 */
  absorbs?: { path: string; title: string }[]
  /** 改一份已有方法时：用户的纠正，或者哪里不对。 */
  reason?: string
}

/** 新建、修改时交上去的内容：配套文件是「路径 → 文本」。 */
export interface ProjectSkillContent {
  title: string
  description: string
  /** 正文，markdown。 */
  body: string
  files: Record<string, string>
}

/** 一个配套文件在清单里的样子；内容要打开那一份才读。 */
export interface SkillFileEntry {
  sha256: string
  size: number
}

/** 谁先写的：芝士整理、人手写、从别处导入。 */
export type SkillOrigin = 'cheese' | 'person' | 'import'

/** 导入时读出来、还没添加的一份。 */
export interface SkillImportPreview extends ProjectSkillContent {
  name: string
  /** 不是文本或太大、不会一起添加的文件。 */
  skipped: string[]
  /** 配套文件里脚本的个数；芝士会在工作电脑上运行它们。 */
  scripts: number
}

export interface ProjectSkill extends Omit<ProjectSkillContent, 'files'> {
  /** 配套文件清单（路径 → 大小等），不带内容。 */
  files: Record<string, SkillFileEntry>
  id: string
  project_id: string
  name: string
  state: 'draft' | 'active'
  origin: SkillOrigin
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

/** 打开一份时读到的：配套文件的内容和历史版本。 */
export interface ProjectSkillDetail extends ProjectSkill {
  contents: Record<string, string>
  revisions: ProjectSkillRevision[]
}

export interface ProjectSkillRevision {
  revision: number
  content: Omit<ProjectSkillContent, 'files'> & { files: Record<string, SkillFileEntry> }
  confirmed_by: string
  note: string
  created_at: string
}

const NAME = /^[a-z0-9][a-z0-9-]{1,47}$/

/**
 * 新建时按名称给出的调用名：名称里有英文就照它拼，没有就取 `skill-<n>` 里第一个没被
 * 占用的。人可以改；给出来的一定合后端的格式。
 */
export function suggestSkillName(title: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
    .replace(/-+$/, '')
  if (NAME.test(slug) && !used.has(slug)) return slug
  const base = NAME.test(slug) ? slug.slice(0, 40) : 'skill'
  for (let n = 1; ; n += 1) {
    const name = `${base}-${n}`
    if (!used.has(name)) return name
  }
}
