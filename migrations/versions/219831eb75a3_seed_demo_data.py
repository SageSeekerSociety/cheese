"""Seed demo data for frontend demonstration.

Revision ID: 219831eb75a3
Revises: a95752502bb0
Create Date: 2026-03-05

All demo users use password: demo12345

Note: password must be >=8 chars and username must be >=4 chars to pass frontend
validation (see cheese-frontend src/views/account/SignIn.vue).
"""

# ruff: noqa: S608

from alembic import op

revision = "219831eb75a3"
down_revision = "a95752502bb0"
branch_labels = None
depends_on = None

# Pre-computed bcrypt hash for "demo12345"
DEMO_PW = "$2b$12$H7AVsXie7xe6vkopOoZGGOKgxwmaiui9XsBXBIsDqllOb.aX.MFHS"

NOW = "NOW()"


def upgrade() -> None:
    # ── Avatars ──────────────────────────────────────────────────────
    op.execute(f"""
        INSERT INTO avatar (id, url, name, avatar_type, usage_count, created_at)
        VALUES
            (1, '/avatars/default.png',  '默认头像',  'default',     0, {NOW}),
            (2, '/avatars/cat.png',      '猫咪',      'predefined',  0, {NOW}),
            (3, '/avatars/dog.png',      '柴犬',      'predefined',  0, {NOW}),
            (4, '/avatars/panda.png',    '熊猫',      'predefined',  0, {NOW}),
            (5, '/avatars/robot.png',    '机器人',    'predefined',  0, {NOW})
        ON CONFLICT (id) DO NOTHING
    """)

    # avatar uses autoincrement
    op.execute("SELECT setval(pg_get_serial_sequence('avatar', 'id'), (SELECT COALESCE(MAX(id),0) FROM avatar), true)")

    # ── Users ────────────────────────────────────────────────────────
    users = [
        (1, "alice",  "alice@demo.test",  "Alice"),
        (2, "bobby",  "bob@demo.test",    "Bob"),
        (3, "carol",  "carol@demo.test",  "Carol"),
        (4, "david",  "david@demo.test",  "David"),
        (5, "evelyn", "eve@demo.test",    "Eve"),
        (6, "frank",  "frank@demo.test",  "Frank"),
        (7, "grace",  "grace@demo.test",  "Grace"),
        (8, "henry",  "henry@demo.test",  "Henry"),
    ]
    for uid, uname, email, nick in users:
        op.execute(f"""
            INSERT INTO "user" (id, username, email, hashed_password, created_at, updated_at)
            VALUES ({uid}, '{uname}', '{email}', '{DEMO_PW}', {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
        avatar_id = ((uid - 1) % 5) + 1
        op.execute(f"""
            INSERT INTO user_profile (user_id, nickname, intro, avatar_id, created_at, updated_at)
            VALUES ({uid}, '{nick}', '知是社区活跃用户', {avatar_id}, {NOW}, {NOW})
            ON CONFLICT DO NOTHING
        """)
    # Advance user_id_seq past our manually-inserted IDs
    op.execute("SELECT setval('user_id_seq', (SELECT COALESCE(MAX(id),0) FROM \"user\"), true)")

    # ── Topics ───────────────────────────────────────────────────────
    topics = [
        (1, "深度学习"),
        (2, "数据科学"),
        (3, "Web 开发"),
        (4, "算法与数据结构"),
        (5, "计算机视觉"),
        (6, "自然语言处理"),
        (7, "云计算"),
        (8, "开源项目"),
        (9, "数据库"),
        (10, "人工智能伦理"),
    ]
    vals = ", ".join(
        f"({tid}, '{name}', 1, {NOW})" for tid, name in topics
    )
    op.execute(f"""
        INSERT INTO topic (id, name, created_by_id, created_at)
        VALUES {vals}
        ON CONFLICT (id) DO NOTHING
    """)
    # topic uses autoincrement (implicit sequence topic_id_seq)
    op.execute("SELECT setval(pg_get_serial_sequence('topic', 'id'), (SELECT COALESCE(MAX(id),0) FROM topic), true)")

    # ── Teams ────────────────────────────────────────────────────────
    teams = [
        (1, "深度学习研究组", "探索前沿 AI 技术", "专注于深度学习模型研究与实践，包括 CV、NLP、强化学习等方向。", 2),
        (2, "全栈开发小队",   "从前端到后端一把梭", "涵盖 Vue、React、FastAPI、Spring Boot 等技术栈的全栈实践团队。", 3),
        (3, "数据分析兴趣组", "用数据讲故事",       "学习数据分析、可视化和统计建模，定期进行 Kaggle 比赛。",      4),
    ]
    for tid, name, intro, desc, avatar_id in teams:
        op.execute(f"""
            INSERT INTO team (id, name, intro, description, avatar_id, created_at, updated_at)
            VALUES ({tid}, '{name}', '{intro}', '{desc}', {avatar_id}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute("SELECT setval('team_seq', (SELECT COALESCE(MAX(id),0) FROM team), true)")

    # Team members: (team_id, user_id, role)  0=OWNER 1=ADMIN 2=MEMBER
    team_members = [
        (1, 1, 0), (1, 2, 1), (1, 3, 2), (1, 4, 2), (1, 5, 2),
        (2, 2, 0), (2, 1, 1), (2, 6, 2), (2, 7, 2),
        (3, 3, 0), (3, 4, 1), (3, 8, 2), (3, 5, 2),
    ]
    for i, (tid, uid, role) in enumerate(team_members, start=1):
        op.execute(f"""
            INSERT INTO team_user_relation (id, team_id, user_id, role, created_at, updated_at)
            VALUES ({i}, {tid}, {uid}, {role}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute(
        "SELECT setval('team_user_relation_seq',"
        " (SELECT COALESCE(MAX(id),0) FROM team_user_relation), true)"
    )

    # ── Spaces ───────────────────────────────────────────────────────
    spaces = [
        (1, "人工智能实践空间", "AI 学习与实践的综合平台",
         "这里汇聚了各种 AI 相关的任务和挑战，从入门到进阶，欢迎参与！", 2),
        (2, "软件工程训练营",   "系统学习软件工程实践",
         "覆盖需求分析、系统设计、编码实现、测试与部署的全流程训练。", 3),
        (3, "数学建模工作坊",   "用数学解决实际问题",
         "定期发布数学建模题目，锻炼建模与论文写作能力。", 4),
    ]
    for sid, name, intro, desc, avatar_id in spaces:
        op.execute(f"""
            INSERT INTO space (id, name, intro, description, avatar_id,
                               enable_rank, announcements, task_templates,
                               created_at, updated_at)
            VALUES ({sid}, '{name}', '{intro}', '{desc}', {avatar_id},
                    false, '[]'::jsonb, '[]'::jsonb,
                    {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute("SELECT setval('space_seq', (SELECT COALESCE(MAX(id),0) FROM space), true)")

    # Space admins
    space_admins = [
        (1, 1, 1, 0),  # space 1, user 1, OWNER
        (2, 1, 2, 1),  # space 1, user 2, ADMIN
        (3, 2, 2, 0),  # space 2, user 2, OWNER
        (4, 3, 3, 0),  # space 3, user 3, OWNER
        (5, 3, 4, 1),  # space 3, user 4, ADMIN
    ]
    for aid, sid, uid, role in space_admins:
        op.execute(f"""
            INSERT INTO space_admin_relation (id, space_id, user_id, role, created_at, updated_at)
            VALUES ({aid}, {sid}, {uid}, {role}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute(
        "SELECT setval('space_admin_relation_seq',"
        " (SELECT COALESCE(MAX(id),0) FROM space_admin_relation), true)"
    )

    # Space categories
    categories = [
        (1, 1, "图像识别",   0),
        (2, 1, "文本生成",   1),
        (3, 2, "前端开发",   0),
        (4, 2, "后端开发",   1),
        (5, 3, "优化问题",   0),
        (6, 3, "统计建模",   1),
    ]
    for cid, sid, name, order in categories:
        op.execute(f"""
            INSERT INTO space_categories (id, space_id, name, display_order, created_at, updated_at)
            VALUES ({cid}, {sid}, '{name}', {order}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute(
        "SELECT setval('space_categories_seq',"
        " (SELECT COALESCE(MAX(id),0) FROM space_categories), true)"
    )

    # ── Tasks ────────────────────────────────────────────────────────
    tasks = [
        (1, "手写数字识别模型训练",
         "使用 MNIST 数据集训练一个手写数字分类器",
         "请使用 PyTorch 或 TensorFlow 实现一个卷积神经网络，在 MNIST 数据集上达到 98% 以上的测试准确率。"
         "需要提交训练代码、模型文件和实验报告。",
         1, 1, 1, 0, 1, 30, 604800000),
        (2, "图像风格迁移应用",
         "实现基于神经网络的图像风格迁移",
         "参考 Gatys et al. 的论文，实现一个图像风格迁移程序。"
         "输入一张内容图片和一张风格图片，输出风格迁移后的结果。",
         1, 1, 2, 0, 1, 20, 1209600000),
        (3, "个人博客系统开发",
         "使用 Vue 3 + FastAPI 开发个人博客",
         "要求实现文章 CRUD、Markdown 渲染、标签分类、评论功能。"
         "前端使用 Vue 3 + Vuetify，后端使用 FastAPI + PostgreSQL。",
         2, 2, 3, 0, 1, 25, 1209600000),
        (4, "RESTful API 设计实践",
         "为一个在线书店设计并实现 RESTful API",
         "设计一套完整的在线书店 API，包括图书管理、用户系统、购物车和订单模块。"
         "要求使用 OpenAPI 规范编写文档，并实现核心接口。",
         2, 2, 4, 0, 1, 30, 604800000),
        (5, "运输路线优化问题",
         "求解车辆路径规划问题 (VRP)",
         "给定若干配送点和车辆容量限制，设计算法求解最短配送路线。"
         "可以使用贪心、遗传算法或整数规划等方法。",
         3, 3, 5, 0, 1, 15, 604800000),
        (6, "疫情数据统计建模",
         "对公开疫情数据进行统计分析与预测",
         "使用 SIR/SEIR 模型或时间序列方法对疫情数据进行拟合与预测。"
         "需要提交分析报告和可视化图表。",
         3, 3, 6, 0, 1, 20, 1209600000),
    ]
    for tid, name, intro, desc, creator_id, sid, cid, stype, approved, plimit, deadline_ms in tasks:
        op.execute(f"""
            INSERT INTO task (id, name, intro, description, creator_id, space_id, category_id,
                              submitter_type, approved, participant_limit,
                              default_deadline, resubmittable, editable,
                              require_real_name, reject_reason, team_locking_policy,
                              created_at, updated_at)
            VALUES ({tid}, '{name}', '{intro}', '{desc}',
                    {creator_id}, {sid}, {cid},
                    {stype}, {approved}, {plimit},
                    {deadline_ms}, false, true,
                    false, '', 'NO_LOCK',
                    {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute("SELECT setval('task_seq', (SELECT COALESCE(MAX(id),0) FROM task), true)")

    # Task submission schemas (what to submit)
    schemas = [
        (1, 0, "训练代码 (.py 或 .ipynb)", 0),
        (1, 1, "实验报告 (PDF)", 0),
        (2, 0, "风格迁移代码", 0),
        (2, 1, "结果图片 (至少 3 张)", 1),
        (3, 0, "项目 GitHub 仓库链接", 0),
        (3, 1, "部署演示链接或截图", 0),
        (4, 0, "OpenAPI 文档", 0),
        (4, 1, "API 实现代码", 0),
        (5, 0, "算法实现代码", 0),
        (5, 1, "求解报告", 0),
        (6, 0, "分析报告 (PDF)", 0),
        (6, 1, "可视化图表", 1),
    ]
    for task_id, idx, desc, stype in schemas:
        op.execute(f"""
            INSERT INTO task_submission_schema (task_id, "index", description, type)
            VALUES ({task_id}, {idx}, '{desc}', {stype})
            ON CONFLICT DO NOTHING
        """)

    # Task-topic relations
    task_topics = [
        (1, 1, 1), (2, 1, 5), (3, 2, 1), (4, 2, 5),
        (5, 3, 3), (6, 4, 3), (7, 5, 4), (8, 6, 2),
    ]
    for rtid, tid, topid in task_topics:
        op.execute(f"""
            INSERT INTO task_topics_relation (id, task_id, topic_id, created_at, updated_at)
            VALUES ({rtid}, {tid}, {topid}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute(
        "SELECT setval('task_topics_relation_seq',"
        " (SELECT COALESCE(MAX(id),0) FROM task_topics_relation), true)"
    )

    # ── Questions ────────────────────────────────────────────────────
    questions = [
        (1, 3, "如何选择合适的深度学习框架？",
         "目前主流的框架有 PyTorch、TensorFlow、JAX 等，各有优劣。想请教大家在实际项目中是如何选择的？",
         0, 5),
        (2, 4, "Python 异步编程最佳实践有哪些？",
         "在使用 asyncio 和 FastAPI 开发时，遇到了一些并发问题。希望了解异步编程的最佳实践和常见陷阱。",
         0, 0),
        (3, 5, "数据库索引优化的实战经验",
         "项目中遇到了查询性能瓶颈，想了解大家在数据库索引设计方面的经验和技巧。",
         0, 10),
        (4, 6, "如何写出高质量的技术文档？",
         "团队的技术文档质量参差不齐，希望了解技术文档编写的最佳实践和工具推荐。",
         0, 0),
        (5, 7, "微服务架构 vs 单体架构的选型建议",
         "新项目启动，团队在微服务和单体架构之间纠结。请问在什么场景下应该选择微服务？",
         0, 8),
    ]
    for qid, creator, title, content, qtype, bounty in questions:
        # Escape single quotes in content
        esc_content = content.replace("'", "''")
        esc_title = title.replace("'", "''")
        op.execute(f"""
            INSERT INTO question (id, created_by_id, title, content, type, bounty, created_at, updated_at)
            VALUES ({qid}, {creator}, '{esc_title}', '{esc_content}', {qtype}, {bounty}, {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)

    # question uses autoincrement
    op.execute("SELECT setval(pg_get_serial_sequence('question', 'id'), (SELECT COALESCE(MAX(id),0) FROM question), true)")

    # Question-topic relations
    q_topics = [
        (1, 1, 1, 3), (2, 1, 5, 3),    # Q1: 深度学习, 计算机视觉
        (3, 2, 3, 4),                     # Q2: Web 开发
        (4, 3, 9, 5),                     # Q3: 数据库
        (5, 4, 8, 6),                     # Q4: 开源项目
        (6, 5, 7, 7), (7, 5, 3, 7),      # Q5: 云计算, Web 开发
    ]
    for rid, qid, topid, creator in q_topics:
        op.execute(f"""
            INSERT INTO question_topic_relation (id, question_id, topic_id, created_by_id, created_at)
            VALUES ({rid}, {qid}, {topid}, {creator}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute("SELECT setval(pg_get_serial_sequence('question_topic_relation', 'id'), (SELECT COALESCE(MAX(id),0) FROM question_topic_relation), true)")

    # ── Answers ──────────────────────────────────────────────────────
    answers = [
        (1, 1, 1, "我推荐 PyTorch，社区活跃、调试方便，特别适合研究和快速原型。"
                   "TensorFlow 更适合生产部署，有 TF Serving 和 TFLite 等工具链。"),
        (2, 1, 2, "JAX 也值得关注，特别是在需要高性能数值计算和自动微分的场景下。"
                   "不过学习曲线比较陡，文档也没有 PyTorch 完善。"),
        (3, 2, 1, "几个关键点：1) 避免在 async 函数中调用阻塞 IO；"
                   "2) 使用 asyncio.gather 并发执行多个任务；"
                   "3) 注意数据库连接池的配置。"),
        (4, 3, 8, "建议关注这几个方面：合理设计联合索引、避免过度索引、"
                   "定期使用 EXPLAIN ANALYZE 检查查询计划、"
                   "考虑使用覆盖索引减少回表。"),
    ]
    for aid, qid, creator, content in answers:
        esc = content.replace("'", "''")
        op.execute(f"""
            INSERT INTO answer (id, question_id, created_by_id, content, created_at, updated_at)
            VALUES ({aid}, {qid}, {creator}, '{esc}', {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)

    # answer uses autoincrement
    op.execute("SELECT setval(pg_get_serial_sequence('answer', 'id'), (SELECT COALESCE(MAX(id),0) FROM answer), true)")

    # Set accepted answer for Q1
    op.execute("UPDATE question SET accepted_answer_id = 1 WHERE id = 1 AND accepted_answer_id IS NULL")

    # ── Knowledge ────────────────────────────────────────────────────
    knowledges = [
        (1, "PyTorch 入门教程汇总", "收集整理的 PyTorch 官方和社区优质教程",
         "TEXT", '{"text": "包含官方教程、动手学深度学习、PyTorch Lightning 等资源链接"}',
         1, 1),
        (2, "FastAPI 项目最佳实践", "FastAPI 项目结构、依赖注入和测试的最佳实践总结",
         "TEXT", '{"text": "项目结构建议采用 domain-driven 分层，使用 Depends 做依赖注入"}',
         2, 2),
        (3, "数据可视化工具对比", "Matplotlib、Seaborn、Plotly、ECharts 对比分析",
         "TEXT", '{"text": "Matplotlib 功能最全但语法繁琐，Plotly 适合交互式图表，ECharts 适合前端集成"}',
         3, 3),
        (4, "Git 工作流规范", "团队 Git 分支管理和 commit 规范",
         "TEXT", '{"text": "推荐使用 trunk-based development，配合 conventional commits 规范"}',
         2, 2),
    ]
    for kid, name, desc, ktype, content, team_id, creator in knowledges:
        esc_name = name.replace("'", "''")
        esc_desc = desc.replace("'", "''")
        op.execute(f"""
            INSERT INTO knowledge (id, name, description, type, content,
                                   team_id, created_by_id, source_type, created_at, updated_at)
            VALUES ({kid}, '{esc_name}', '{esc_desc}', '{ktype}', '{content}'::jsonb,
                    {team_id}, {creator}, 'MANUAL', {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute("SELECT setval('knowledge_seq', (SELECT COALESCE(MAX(id),0) FROM knowledge), true)")

    # Knowledge labels
    k_labels = [
        (1, 1, "PyTorch"), (2, 1, "教程"), (3, 2, "FastAPI"), (4, 2, "Python"),
        (5, 3, "可视化"), (6, 3, "数据分析"), (7, 4, "Git"), (8, 4, "团队协作"),
    ]
    for lid, kid, label in k_labels:
        op.execute(f"""
            INSERT INTO knowledge_label (id, knowledge_id, label, created_at, updated_at)
            VALUES ({lid}, {kid}, '{label}', {NOW}, {NOW})
            ON CONFLICT (id) DO NOTHING
        """)
    op.execute(
        "SELECT setval('knowledge_label_seq',"
        " (SELECT COALESCE(MAX(id),0) FROM knowledge_label), true)"
    )


def downgrade() -> None:
    # Remove seeded data in reverse FK order
    op.execute("DELETE FROM knowledge_label WHERE id <= 8")
    op.execute("DELETE FROM knowledge WHERE id <= 4")
    op.execute("DELETE FROM question_topic_relation WHERE id <= 7")
    op.execute("DELETE FROM answer WHERE id <= 4")
    op.execute("UPDATE question SET accepted_answer_id = NULL WHERE id = 1")
    op.execute("DELETE FROM question WHERE id <= 5")
    op.execute("DELETE FROM task_topics_relation WHERE id <= 8")
    op.execute("DELETE FROM task_submission_schema WHERE task_id <= 6")
    op.execute("DELETE FROM task WHERE id <= 6")
    op.execute("DELETE FROM space_categories WHERE id <= 6")
    op.execute("DELETE FROM space_admin_relation WHERE id <= 5")
    op.execute("DELETE FROM space WHERE id <= 3")
    op.execute("DELETE FROM team_user_relation WHERE id <= 13")
    op.execute("DELETE FROM team WHERE id <= 3")
    op.execute("DELETE FROM topic WHERE id <= 10")
    op.execute("DELETE FROM user_profile WHERE user_id <= 8")
    op.execute("DELETE FROM \"user\" WHERE id <= 8")
    op.execute("DELETE FROM avatar WHERE id <= 5")
