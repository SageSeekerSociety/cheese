/**
 * feedbackDiagram.ts — 反馈功能的表结构与架构图数据。
 *
 * 内容抄自 `docs/topics/反馈功能后端设计-方案稿.md`：§2.2 / §2.3 / §2.4 是表，
 * §2.5 是状态集，§5.8 是 agent 那条入口。**图里不引入方案稿没有的东西** ——
 * 图比文字更容易被当成承诺，多一根线就多一个「那就这么做吧」。
 *
 * 方案稿改了这张图就要跟着改。两边不一致时，方案稿是准的。
 */
import type { DiagramArch, DiagramEr } from './diagramSpec'

export const feedbackEr: DiagramEr = {
  title: '反馈功能的表与关系',
  subtitle: '五张子表都挂在 feedback 上；话题与项目只是可空的上下文指针（删了置空，不参与鉴权）。虚线表示没有真外键。',
  entities: [
    {
      id: 'topics',
      title: 'topics',
      group: '上下文指针',
      note: '已有表。删话题不会删反馈。',
      columns: [{ name: 'id', type: 'Uuid', badge: 'PK' }],
    },
    {
      id: 'projects',
      title: 'projects',
      group: '上下文指针',
      note: '已有表。同上。',
      columns: [{ name: 'id', type: 'Uuid', badge: 'PK' }],
    },
    {
      id: 'feedback',
      title: 'feedback',
      group: '主表',
      isNew: true,
      note: '六个 Text 字段是提交那一刻的快照，不是外键。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'display_no', type: 'Integer', badge: 'UQ', note: 'FB-1042' },
        { name: 'title', type: 'String(300)' },
        { name: 'summary', type: 'String(300)' },
        { name: 'kind', type: 'Enum(16)', note: 'bug/suggestion/other' },
        { name: 'status', type: 'Enum(16)', note: '五档，见下' },
        { name: 'visibility', type: 'Enum(16)', note: 'public/private' },
        { name: 'priority', type: 'Enum(16)' },
        { name: 'security', type: 'Boolean' },
        { name: 'problem/why/expectation', type: 'Text' },
        { name: 'what_happened/repro/evidence', type: 'Text', note: '可空' },
        { name: 'logs', type: 'Text', note: '可空' },
        { name: 'session_id', type: 'String(64)', note: '快照，非外键' },
        { name: 'environment', type: 'String(255)', note: '快照' },
        { name: 'author_handle', type: 'String(64)', badge: 'IDX' },
        { name: 'author_user_id', type: 'Integer' },
        { name: 'author_is_agent', type: 'Boolean' },
        { name: 'submitted_by_handle', type: 'String(64)' },
        { name: 'assignee_handle', type: 'String(64)', badge: 'IDX' },
        { name: 'topic_id', type: 'Uuid', badge: 'FK', note: '可空' },
        { name: 'project_id', type: 'Uuid', badge: 'FK', note: '可空' },
        { name: 'tags', type: 'JSON', note: 'MVP 不建标签表' },
        { name: 'created_at/updated_at', type: 'Timestamps' },
        { name: 'deleted_at', type: 'DateTime', note: '软删' },
      ],
    },
    {
      id: 'feedback_supports',
      title: 'feedback_supports',
      group: '子表',
      isNew: true,
      note: '唯一约束 (feedback_id, author_handle)：一人一票。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'author_handle', type: 'String(64)', badge: 'IDX' },
        { name: 'created_at', type: 'DateTime' },
      ],
    },
    {
      id: 'feedback_comments',
      title: 'feedback_comments',
      group: '子表',
      isNew: true,
      note: '不复用 1.0 的 comment 表：那张表的主键是 Integer，我们的主键是 uuid。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'author_handle', type: 'String(64)' },
        { name: 'author_is_agent', type: 'Boolean' },
        { name: 'body', type: 'Text' },
        { name: 'created_at/deleted_at', type: 'DateTime' },
      ],
    },
    {
      id: 'feedback_timeline',
      title: 'feedback_timeline',
      group: '子表',
      isNew: true,
      note: 'append-only。改状态与追加事件必须同一个事务。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'status', type: 'Enum(16)' },
        { name: 'at', type: 'DateTime', badge: 'IDX' },
        { name: 'by_handle', type: 'String(64)' },
      ],
    },
    {
      id: 'feedback_notes',
      title: 'feedback_notes',
      group: '子表',
      isNew: true,
      note: '内部备注。主表不存备注字符串列——那样两个管理员会互相覆盖。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'author_handle', type: 'String(64)' },
        { name: 'body', type: 'Text' },
        { name: 'created_at', type: 'DateTime' },
      ],
    },
    {
      id: 'feedback_attachments',
      title: 'feedback_attachments',
      group: '子表',
      isNew: true,
      note: '存储方式待定（§8.7）。硬要求：私密反馈的附件不能有公开 URL。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'name/url/mime_type', type: 'String' },
        { name: 'size', type: 'Integer' },
        { name: 'created_at', type: 'DateTime' },
      ],
    },
  ],
  relations: [
    { from: 'feedback_supports', to: 'feedback', cardinality: 'N-1', label: '支持' },
    { from: 'feedback_comments', to: 'feedback', cardinality: 'N-1', label: '评论' },
    { from: 'feedback_timeline', to: 'feedback', cardinality: 'N-1', label: '状态事件' },
    { from: 'feedback_notes', to: 'feedback', cardinality: 'N-1', label: '备注' },
    { from: 'feedback_attachments', to: 'feedback', cardinality: 'N-1', label: '附件' },
    { from: 'feedback', to: 'topics', cardinality: 'N-1', soft: true },
    { from: 'feedback', to: 'projects', cardinality: 'N-1', soft: true },
  ],
}

export const feedbackArch: DiagramArch = {
  title: '反馈从哪来、到哪去',
  subtitle: '三个入口共用一个上报契约：人写、agent 提议、管理端处理。agent 那条不直接落库——先成为提案，等人确认。',
  lanes: [
    {
      id: 'harness',
      title: 'Harness',
      nodes: [
        {
          id: 'cc',
          label: '芝士 / Claude Code',
          sub: 'MCP 工具 cheese_feedback_propose',
          kind: 'harness',
        },
        {
          id: 'other',
          label: '其他 harness',
          sub: '同一个工具，不各写一套',
          kind: 'harness',
        },
      ],
    },
    {
      id: 'human',
      title: '人这一侧',
      nodes: [
        { id: 'center', label: '反馈中心', sub: '公开列表 + 我的私密', kind: 'plain' },
        { id: 'drawer', label: '提交抽屉', sub: '唯一的手动写入口', kind: 'plain' },
        { id: 'admin', label: '管理后台', sub: '独立网页，仅平台管理员', kind: 'admin' },
      ],
    },
    {
      id: 'entry',
      title: '统一入口',
      nodes: [
        {
          id: 'proposal',
          label: '反馈提案',
          sub: '指纹去重 + 限流 + 拒绝记忆，不直接写 feedback',
          kind: 'entry',
        },
      ],
    },
    {
      id: 'api',
      title: '服务端',
      nodes: [
        { id: 'route', label: '/feedback 路由', sub: 'Route → Service → Repository', kind: 'api' },
        { id: 'service', label: 'feedback service', sub: '状态与时间线同事务写', kind: 'api' },
        { id: 'notify', label: '通知 / 未读', sub: '新进展推给作者与支持者', kind: 'api' },
      ],
    },
    {
      id: 'store',
      title: '存储',
      nodes: [
        { id: 'db', label: 'feedback + 5 张子表', sub: 'PostgreSQL', kind: 'store' },
        { id: 'busy', label: '附件对象存储', sub: '私密的不能有公开 URL', kind: 'store' },
      ],
    },
  ],
  edges: [
    { from: 'cc', to: 'proposal', label: '提议' },
    { from: 'other', to: 'proposal', label: '提议' },
    { from: 'proposal', to: 'route', label: '人确认后提交' },
    { from: 'center', to: 'route' },
    { from: 'drawer', to: 'route' },
    { from: 'admin', to: 'route', label: '管理员操作' },
    { from: 'route', to: 'service' },
    { from: 'service', to: 'db' },
    { from: 'service', to: 'busy' },
    { from: 'service', to: 'notify' },
  ],
}
