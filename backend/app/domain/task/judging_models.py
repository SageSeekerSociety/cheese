"""测试点与评测结果的表（方案 §11）。

从 ``models.py`` 里分出来：那一个模块已经 594 行，评测这一块自成一组 —— 三张表
（测试套、测试用例、评测记录）只被评测这条线读写，和题目、领取、提交、评审那几张
表没有耦合。

三张表都在 ``app/models.py`` 里登记，Alembic 与测试建表才看得到。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

task_test_suite_seq = Sequence("task_test_suite_seq")
task_test_case_seq = Sequence("task_test_case_seq")
task_judge_run_seq = Sequence("task_judge_run_seq")


class TaskTestSuite(Base):
    """一道题一个阶段的那一套测试点（方案 §数据结构）。

    ``kind`` 是形态：``IO``（输入/输出用例）、``SCRIPT``（出题人的测试文件）、
    ``COMMAND``（项目自带判分程序）。第 1 步只出 ``IO``，表结构三种都留好，后两种在
    第 2 步接上 —— 那时只填指令和文件，不再改表。

    改测试点和改评分要点一样：整份替换，``revision`` 跟着加一，评测记录记下它跑的是
    第几版（``TaskJudgeRun.suite_revision``），于是已出的结果不会随改题而变。
    """

    __tablename__ = "task_test_suite"
    __table_args__ = (
        UniqueConstraint("task_id", "stage", name="uq_task_test_suite_task_id_stage"),
        CheckConstraint(
            "kind IN ('IO', 'SCRIPT', 'COMMAND')", name="ck_task_test_suite_kind"
        ),
        CheckConstraint(
            "compare IN ('TRIM_TRAILING', 'EXACT', 'NUMERIC', 'IGNORE_CASE')",
            name="ck_task_test_suite_compare",
        ),
        CheckConstraint(
            "score_source IS NULL OR score_source IN ('LINE', 'JSON')",
            name="ck_task_test_suite_score_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_test_suite_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "task.id", ondelete="CASCADE", name="fk_task_test_suite_task_id_task"
        ),
        nullable=False,
        index=True,
    )
    #: 属于哪个阶段（``TaskStage.index``）。不分阶段的题是 0。
    stage: Mapped[int] = mapped_column(
        "stage", Integer, nullable=False, default=0, server_default="0"
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="IO", server_default="IO"
    )
    #: 运行环境镜像的名字，见 ``test_suite.RUNTIME_KEYS``。
    runtime: Mapped[str] = mapped_column(
        String(32), nullable=False, default="python3.12", server_default="python3.12"
    )
    #: 学生代码在仓库里的目录，空 = 仓库根。
    code_dir: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    compile_cmd: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    run_cmd: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    #: 一个用例的时限（毫秒）。
    time_limit_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default="1000"
    )
    memory_mb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=256, server_default="256"
    )
    compare: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="TRIM_TRAILING",
        server_default="TRIM_TRAILING",
    )
    #: ``compare = 'NUMERIC'`` 时的相对误差。
    tolerance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    #: 第几版，从 1 数；改一次加一。
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: 关掉后学生只看得到「隐藏用例 N」。
    show_hidden_names: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: ``COMMAND`` 形态按顺序跑的命令（第 2 步）。
    commands: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: ``COMMAND`` 形态的分数从哪读：命令打出的那一行（``LINE``）或仓库根的
    #: score.json（``JSON``）。其余形态为 NULL。
    score_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: ``COMMAND`` 形态判分前用原版覆盖回去的文件。
    protected_paths: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: ``SCRIPT``/``COMMAND`` 形态的测试文件：``[{path, location, key, bytes}]``，
    #: 字节在私有存储里（``app.core.blob_store``）。
    test_files: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: ``SCRIPT`` 形态是否按学生仓库的依赖清单装依赖。
    install_deps: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: 整轮时限（秒），``SCRIPT`` 默认 120、``COMMAND`` 默认 300；``IO`` 为 NULL。
    total_time_limit_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 允许学生看到的判分程序输出行数；0 = 不给。
    student_log_lines: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: 每人每天的自测次数；NULL = 不开自测。
    self_test_daily: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TaskTestCase(Base):
    """一套测试里的一个用例。

    ``IO`` 形态一行一个用例，输入与期望输出不止存文本：超过 64 KB 的那些（以及以后
    上传的文件）放私有存储，这一行只记 ``location``/``key``/``bytes``。第 1 步只内联
    文本，一样走这条路 —— 隐藏用例的期望输出任何时候都不进学生项目和评测沙箱。

    ``SCRIPT`` 形态一行一个测试名和它的分值（``test_id``），输入输出那几列是空的。
    """

    __tablename__ = "task_test_case"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('SAMPLE', 'HIDDEN')", name="ck_task_test_case_visibility"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_test_case_seq, primary_key=True)
    suite_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "task_test_suite.id",
            ondelete="CASCADE",
            name="fk_task_test_case_suite_id_task_test_suite",
        ),
        nullable=False,
        index=True,
    )
    #: 在测试套里的次序，从 0 数。
    index: Mapped[int] = mapped_column("index", Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 用例之间的轻重；最后按比例折算到评分要点那一项的分值。
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: ``SAMPLE`` 学生看得到输入输出；``HIDDEN`` 只看到过没过。
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SAMPLE", server_default="SAMPLE"
    )
    #: ``SCRIPT`` 形态的测试名（pytest 的 ``test_x``、JUnit 的方法名）。
    test_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    input_location: Mapped[str | None] = mapped_column(String(16), nullable=True)
    input_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    input_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_location: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expected_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    expected_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: 改题写新行、旧行标取代（同资料库那一列的做法）：已出的评测结果指着老行，老行
    #: 不能就这么没了。
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class TaskJudgeRun(Base):
    """一次评测：跑哪一版代码、用第几版测试、结果如何。

    ``purpose`` 是 ``SUBMISSION``（学生提交后自动进队）或 ``TRIAL``（出题人用参考
    答案试跑、学生自测）—— 后者 ``submission_id`` 为空，代之以 ``trial_spec``（哪一
    个分支、哪一版代码、谁发起的）。

    这条记录**不是评审**：现在的规则是一版提交有一份评审判通过就算通过，把评测写进
    评审会让没批改的作业直接变成已通过（方案 §特殊情况）。所以它单独存，老师批改时
    由 ``grading`` 合成进评分要点里那一项的分。

    排队与重试照 ``task.starter_jobs`` 那套「表加租约」：``state = 'queued'`` 的行由
    评测主机上的领取程序领走，``lease_until`` 到期没干完就还回去。
    """

    __tablename__ = "task_judge_run"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('SUBMISSION', 'TRIAL')", name="ck_task_judge_run_purpose"
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed')",
            name="ck_task_judge_run_state",
        ),
        # 领任务就是「拿最早那个排队的」：这条部分索引正是那个读法。
        Index(
            "ix_task_judge_run_queued",
            "state",
            "created_at",
            postgresql_where=text("state = 'queued'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_judge_run_seq, primary_key=True)
    #: 哪道题、哪一套测试。出题人在表单里试跑（``purpose = 'TRIAL'``）时题目还没发布，
    #: 这两格为空，跑什么写在 ``trial_spec`` 里。
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "task.id", ondelete="CASCADE", name="fk_task_judge_run_task_id_task"
        ),
        nullable=True,
        index=True,
    )
    suite_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "task_test_suite.id",
            ondelete="CASCADE",
            name="fk_task_judge_run_suite_id_task_test_suite",
        ),
        nullable=True,
        index=True,
    )
    #: 算力记在哪个空间头上（试跑那一行用；提交的那一行从题目读）。不是外键，同
    #: ``project_id``。
    space_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 跑的是测试套的第几版（``TaskTestSuite.revision``）。
    suite_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 哪一版提交；试跑与自测为空。
    submission_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "task_submission.id",
            ondelete="CASCADE",
            name="fk_task_judge_run_submission_id_task_submission",
        ),
        nullable=True,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUBMISSION", server_default="SUBMISSION"
    )
    #: 试跑要跑什么：测试套的设置、用例和参考代码在私有存储里的位置
    #: （``app.domain.judge.trials``）。等提交跑的那条为空。
    trial_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 代码从哪个项目取（学生项目的 id）。不是外键：项目删了，这份结果照旧是记录。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: 评测的是这一版代码（commit）。
    code_sha: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    #: 谁发起的（``user.id``）。
    requested_by: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 哪台评测主机在跑（``device.device_id``）。
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 评测主机上花的毫秒数。
    machine_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 编译/运行的输出，截断后。
    compile_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 逐个用例的结果：``[{index, name, status, ms, memoryKb, points, ...}]``。
    results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: 通过用例的分值之和 ÷ 全部用例分值之和，算好的两个数。
    passed_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 失败那一条的句子（``{key, params}``），照 ``starter_jobs._sentence``。
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 这次评测花的算力，记在题目所在空间的付款团队头上。
    credits: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )
