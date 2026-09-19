/**
 * diagramSpec.ts — 图表的数据形状。
 *
 * 只有类型，没有数据：数据在 feedbackDiagram.ts 里，来源是后端方案稿。
 * 分开是为了让「谁画的」和「画成什么样」互不牵扯 —— 数据是从文档抄来的事实，
 * 渲染是这个组件库的事。
 *
 * ER 图和架构图刻意用**同一个形状的 id 引用**（字符串 id 互相指），不嵌对象：
 * 嵌对象的话，一张表出现在两个分组里就要复制两份，改一处漏一处不会报错。
 */

/** 表里的一列。`badge` 只放短标记（PK / FK / IDX / UQ / NULL），别塞句子。 */
export interface DiagramColumn {
  name: string
  /** 类型串，照抄迁移里的写法（如 String(64)、Boolean、DateTime）。 */
  type?: string
  badge?: string
  /** 一行以内的补充。长了解释请写进方案稿，不要写进图里。 */
  note?: string
}

export interface DiagramEntity {
  /** 稳定 id：表名就是最好的 id，别再造一套。 */
  id: string
  title: string
  /** 分组决定它落在哪一栏。同一分组的表排在同一列里。 */
  group?: string
  /** 新建的表还是仓库里已有的表 —— 图上要用不同的描边区分。 */
  isNew?: boolean
  note?: string
  columns: DiagramColumn[]
}

export interface DiagramRelation {
  from: string
  to: string
  label?: string
  /** 1-1 / 1-N / N-N。写成别的值图上会原样显示，但不要这么干。 */
  cardinality?: string
  /** 逻辑关联（没有真外键）。图上画成虚线。 */
  soft?: boolean
}

export interface DiagramEr {
  title?: string
  subtitle?: string
  entities: DiagramEntity[]
  relations: DiagramRelation[]
}

export interface DiagramNode {
  id: string
  label: string
  /** 节点下面那行小字，一句话。 */
  sub?: string
  /** 给节点定色的类别，见架构图里的 NODE_KIND。 */
  kind?: string
  note?: string
}

export interface DiagramLane {
  id: string
  title: string
  nodes: DiagramNode[]
}

export interface DiagramEdge {
  from: string
  to: string
  /** 这条线上传的是什么。 */
  label?: string
  kind?: string
}

export interface DiagramArch {
  title?: string
  subtitle?: string
  lanes: DiagramLane[]
  edges: DiagramEdge[]
}

/**
 * 把 `1-N` 这样的基数串拆成两端各自该显示的记号。
 *
 * 拆不出来就两端都返回空串 —— 图上少一个记号，好过瞎标一个把关系说反。
 */
export function cardinalityEnds(cardinality?: string): { from: string; to: string } {
  if (!cardinality) return { from: '', to: '' }
  const parts = cardinality.split('-').map((part) => part.trim().toUpperCase())
  if (parts.length !== 2) return { from: '', to: '' }
  const ok = (v: string) => v === '1' || v === 'N' || v === 'M'
  if (!ok(parts[0]) || !ok(parts[1])) return { from: '', to: '' }
  const norm = (v: string) => (v === 'M' ? 'N' : v)
  return { from: norm(parts[0]), to: norm(parts[1]) }
}
