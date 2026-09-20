/**
 * feedbackDiagram.ts — 反馈功能的表结构与架构图数据。
 *
 * **这张图画的是已经落地的东西**：表、关系、链路都对着
 * `backend/app/domain/feedback/` 与 `backend/app/api/routes/feedback*.py` 核过，
 * 两侧不一致时以代码为准。方案稿（`docs/topics/反馈功能后端设计-方案稿.md`）是
 * 这些判断的来龙去脉，图不再引用它还没实现的那些表 —— 图比文字更容易被当成承诺，
 * 多画一根线就多一个「那就这么做吧」。
 *
 * 方案稿里写、但**没有实现**的东西不画：附件表、标签关联表、`alerts` 那一套投递。
 * 它们的去向写在方案稿 §8.1 / §8.2 里。
 */
import type { DiagramArch, DiagramEr } from './diagramSpec'

export const feedbackEr: DiagramEr = {
  title: '反馈功能的表与关系',
  subtitle:
    '新建七张表：六张挂在 feedback 上，拒绝记忆挂在话题上。话题、项目、会话只是可空的上下文指针。虚线表示没有真外键。',
  entities: [
    {
      id: 'topics',
      title: 'topics',
      group: '上下文与既有表',
      note: '既有表。删话题不会删反馈，只把 topic_id 置空；但会删掉那条话题的拒绝记忆。',
      columns: [{ name: 'id', type: 'Uuid', badge: 'PK' }],
    },
    {
      id: 'projects',
      title: 'projects',
      group: '上下文与既有表',
      note: '既有表。同上；反馈中心是平台级的，不按项目隔离。',
      columns: [{ name: 'id', type: 'Uuid', badge: 'PK' }],
    },
    {
      id: 'agent_sessions',
      title: 'agent_sessions',
      group: '上下文与既有表',
      note: '既有表。反馈只存 session_id 快照，刻意不建外键：会话删了，反馈要留下。',
      columns: [{ name: 'id', type: 'Uuid', badge: 'PK' }],
    },
    {
      id: 'alerts',
      title: 'alerts',
      group: '上下文与既有表',
      note: '既有表，**没有用它**：project_id 非空，而反馈没有项目（§6.2）。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'project_id', type: 'Uuid', badge: 'FK', note: '非空，所以绕开' },
      ],
    },
    {
      id: 'feedback',
      title: 'feedback',
      group: '反馈主体',
      isNew: true,
      note: '索引：(visibility, status, created_at)、(visibility, security, created_at)、author / submitted_by / assignee。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'display_no', type: 'Integer', badge: 'SEQ', note: 'FB-1042，接口里叫 display_id' },
        { name: 'title', type: 'String(300)' },
        { name: 'summary', type: 'String(300)', note: '列表那一句' },
        { name: 'kind', type: 'Enum(16)', note: 'bug/suggestion/other' },
        { name: 'status', type: 'Enum(16)', note: '五档，与 timeline 同事务写' },
        { name: 'visibility', type: 'Enum(16)', note: 'public/private' },
        { name: 'security', type: 'Boolean', note: 'private 之下的一层收窄' },
        { name: 'priority', type: 'Enum(16)' },
        { name: 'problem / why / expectation', type: 'Text', note: '人写的三段' },
        { name: 'what_happened / repro / evidence', type: 'Text', note: 'agent 现场三段，可空' },
        { name: 'logs', type: 'Text', note: '可空，需脱敏' },
        { name: 'session_id', type: 'String(64)', note: '快照，非外键' },
        { name: 'environment', type: 'String(255)', note: '快照' },
        { name: 'author_handle', type: 'String(64)', badge: 'IDX', note: '发现者' },
        { name: 'author_user_id', type: 'Integer' },
        { name: 'author_is_agent', type: 'Boolean', note: '与 submitted_by 是两个人' },
        { name: 'submitted_by_handle', type: 'String(64)', note: '谁按下提交的' },
        { name: 'assignee_handle', type: 'String(64)', badge: 'IDX', note: '部分索引' },
        { name: 'topic_id', type: 'Uuid', badge: 'FK', note: '可空，SET NULL' },
        { name: 'project_id', type: 'Uuid', badge: 'FK', note: '可空，SET NULL' },
        { name: 'tags', type: 'JSON', note: '不建标签表' },
        { name: 'created_at / updated_at', type: 'Timestamps' },
        { name: 'deleted_at', type: 'DateTime', note: '留着，还没有端点会写它' },
      ],
    },
    {
      id: 'feedback_supports',
      title: 'feedback_supports',
      group: '评论与支持',
      isNew: true,
      note: '唯一约束 (feedback_id, author_handle)：一人一票。私密反馈不给这个按钮。',
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
      group: '评论与支持',
      isNew: true,
      note: '不复用 1.0 的 comment 表：那张是多态 + int 主键，这里要 uuid。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'parent_id', type: 'Uuid', badge: 'FK', note: '只指顶层评论，可空' },
        { name: 'author_handle', type: 'String(64)' },
        { name: 'author_is_agent', type: 'Boolean' },
        { name: 'body', type: 'Text' },
        { name: 'created_at / deleted_at', type: 'DateTime' },
      ],
    },
    {
      id: 'feedback_timeline',
      title: 'feedback_timeline',
      group: '流程与通知',
      isNew: true,
      note: 'append-only，允许回退再推进。改状态与追加事件必须同一个事务。',
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
      group: '流程与通知',
      isNew: true,
      note: '内部备注，只有管理员看得到。主表不存备注字符串列——那样两个管理员会互相覆盖。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'feedback_id', type: 'Uuid', badge: 'FK', note: 'CASCADE' },
        { name: 'author_handle', type: 'String(64)' },
        { name: 'body', type: 'Text' },
        { name: 'created_at', type: 'DateTime' },
      ],
    },
    {
      id: 'feedback_read_states',
      title: 'feedback_read_states',
      group: '流程与通知',
      isNew: true,
      note: '一人一条游标：未读是「比我上次读的时间更新的动静」，不是逐条已读表。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'user_handle', type: 'String(64)', badge: 'UQ', note: '一人一行' },
        { name: 'last_read_at', type: 'DateTime' },
        { name: 'created_at / updated_at', type: 'Timestamps' },
      ],
    },
    {
      id: 'feedback_proposal_dismissals',
      title: 'feedback_proposal_dismissals',
      group: '流程与通知',
      isNew: true,
      note: '「这个不用」只该问一次。指纹不是 block id：换个说法提上来，人不想再看第二遍。',
      columns: [
        { name: 'id', type: 'Uuid', badge: 'PK' },
        { name: 'topic_id', type: 'Uuid', badge: 'FK', note: 'CASCADE，范围是话题' },
        { name: 'fingerprint', type: 'String(64)', badge: 'UQ', note: '唯一约束 (topic_id, fingerprint)' },
        { name: 'dismissed_by_handle', type: 'String(64)' },
        { name: 'created_at', type: 'DateTime' },
      ],
    },
  ],
  relations: [
    { from: 'feedback', to: 'topics', cardinality: 'N-1', soft: true, label: '上下文' },
    { from: 'feedback', to: 'projects', cardinality: 'N-1', soft: true, label: '上下文' },
    { from: 'feedback', to: 'agent_sessions', cardinality: 'N-1', soft: true, label: '快照' },
    { from: 'feedback_supports', to: 'feedback', cardinality: 'N-1', label: '支持' },
    { from: 'feedback_comments', to: 'feedback', cardinality: 'N-1', label: '评论' },
    { from: 'feedback_comments', to: 'feedback_comments', cardinality: 'N-1', label: '回复' },
    { from: 'feedback_timeline', to: 'feedback', cardinality: 'N-1', label: '状态事件' },
    { from: 'feedback_notes', to: 'feedback', cardinality: 'N-1', label: '备注' },
    { from: 'feedback_proposal_dismissals', to: 'topics', cardinality: 'N-1', label: '拒绝记忆' },
    { from: 'feedback_read_states', to: 'feedback', cardinality: 'N-1', soft: true, label: '未读' },
  ],
}

export const feedbackArch: DiagramArch = {
  title: '反馈从哪来、到哪去',
  subtitle: '两条提交通道最后都落到 feedback 表：人直接写，agent 先成为提案卡、由人在卡上按采纳。',
  lanes: [
    {
      id: 'harness',
      title: '提交方',
      nodes: [
        { id: 'cc', label: 'Claude Code', sub: 'SendFeedback 提草稿', kind: 'harness' },
        { id: 'agent', label: '芝士会话内反馈卡', sub: '轮次里主动调工具', kind: 'harness' },
        { id: 'manual', label: '用户手动提交', sub: '反馈中心的提交抽屉', kind: 'harness' },
        { id: 'other', label: '其他 harness', sub: '同一个 cheese 子命令', kind: 'harness' },
        { id: 'patrol', label: '无屏巡检轮次', sub: '走告警，不给工具', kind: 'note' },
      ],
    },
    {
      id: 'ccdraft',
      title: '本地草稿',
      nodes: [
        { id: 'ccq', label: '草稿队列', sub: '每会话 ≤3 张', kind: 'harness' },
        { id: 'ccr', label: '审阅卡片', sub: '1 审阅 2 发送 0 忽略', kind: 'harness' },
      ],
    },
    {
      id: 'contract',
      title: '上报契约',
      nodes: [
        { id: 'propose', label: '反馈提案工具', sub: 'cheese_feedback_propose', kind: 'tool' },
        { id: 'render', label: '渲染提案卡', sub: '一条消息块，不是旁白', kind: 'tool' },
        { id: 'submit', label: '统一提交管道', sub: 'POST /feedback', kind: 'tool' },
      ],
    },
    {
      id: 'server',
      title: '平台服务端',
      nodes: [
        { id: 'auth', label: '鉴权', sub: '两族凭据都认的 Actor', kind: 'service' },
        { id: 'quota', label: '每日配额', sub: '每话题 2 条，可配置', kind: 'service' },
        { id: 'dedup', label: '指纹去重', sub: '发生了什么 + 怎么复现', kind: 'service' },
        { id: 'api_u', label: '用户侧 API', sub: '列表 · 详情 · 评论', kind: 'service' },
        { id: 'api_a', label: '管理侧 API', sub: '白名单才进得来', kind: 'service' },
        { id: 'meta', label: '元数据接口', sub: 'GET /feedback/meta', kind: 'service' },
      ],
    },
    {
      id: 'storage',
      title: '存储',
      nodes: [
        { id: 'store', label: '反馈主表与子表', sub: 'PostgreSQL，单 head', kind: 'store' },
        { id: 'unread', label: '未读游标', sub: '一人一条，全库', kind: 'store' },
        { id: 'dismiss', label: '拒绝记忆', sub: '按话题 + 指纹', kind: 'store' },
        { id: 'alertsx', label: '既有 alerts', sub: '巡检的问题走它', kind: 'store' },
      ],
    },
    {
      id: 'frontend',
      title: '界面',
      nodes: [
        { id: 'center', label: '反馈中心', sub: '4 个 Tab + 未读', kind: 'ui' },
        { id: 'detail', label: '反馈详情', sub: '支持 · 评论 · 时间线', kind: 'ui' },
        { id: 'drawer', label: '提交抽屉', sub: '唯一的手动写入口', kind: 'ui' },
        { id: 'card_ui', label: '会话内反馈卡', sub: '挂在对话流里', kind: 'ui' },
        { id: 'admin', label: '管理后台', sub: '同一 SPA，仅管理员', kind: 'ui' },
      ],
    },
  ],
  edges: [
    { from: 'cc', to: 'ccq', label: '进本地队列' },
    { from: 'ccq', to: 'ccr', label: '出示卡片' },
    { from: 'ccr', to: 'submit', label: '人确认后送出' },
    { from: 'manual', to: 'submit', label: '同一条管道' },
    { from: 'agent', to: 'propose', label: '主动调用' },
    { from: 'other', to: 'propose', label: '同一个工具' },
    { from: 'patrol', to: 'alertsx', label: '走告警' },
    { from: 'propose', to: 'quota', label: '过闸' },
    { from: 'quota', to: 'dedup', label: '查指纹' },
    { from: 'dedup', to: 'render', label: '渲染成卡' },
    { from: 'render', to: 'card_ui', label: '挂到对话流' },
    { from: 'card_ui', to: 'drawer', label: '点采纳' },
    { from: 'drawer', to: 'submit', label: '人按下提交' },
    { from: 'submit', to: 'auth', label: '鉴权' },
    { from: 'auth', to: 'api_u', label: '放行' },
    { from: 'api_u', to: 'store', label: '写入' },
    { from: 'api_a', to: 'store', label: '改状态' },
    { from: 'store', to: 'unread', label: '推游标' },
    { from: 'store', to: 'dismiss', label: '记指纹' },
    { from: 'unread', to: 'center', label: '未读计数' },
    { from: 'center', to: 'api_u', label: '列表查询' },
    { from: 'detail', to: 'api_u', label: '详情与评论' },
    { from: 'drawer', to: 'api_u', label: '提交' },
    { from: 'admin', to: 'api_a', label: '管理操作' },
    { from: 'meta', to: 'center', label: '词表与阈值' },
    { from: 'meta', to: 'admin', label: '是不是管理员' },
  ],
}
