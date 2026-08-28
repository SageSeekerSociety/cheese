"""架构守卫：领域包不许直接 import 别的领域的 repository 模块。

正路是 service → 对方的 service。直接摸对方的 repository 会把「数据访问」这层的
约束（谁能读、读完要不要补别的字段、要不要发通知）绕过去。本领域内部随便 import，
不受限。

**这道守卫治的是分层纪律，不是依赖环——别指望它把环拆没。** 按正路改完，包和包之间
那条边还在，只是从 repository 层挪到了 services 层；包级依赖图一条边都不会少。真要解
环得消除依赖本身（倒置控制、把数据传进来、把逻辑搬家），那是另一件事，成本高得多。

实测（2026-08-11，42 个领域包 / 224 条跨包 import）：``models`` 77 条、
``repositories`` 77 条、``services`` 16 条且无环。把跨域 repository 边整个**删掉**
（不是改道），双向依赖对 20 → 9、强连通分量 26 个包 → 18 个；把 ``models`` 边也删掉
才降到两个 3 包的小环。也就是说 ORM 关系那半边贡献的环一样多，而那半边是设计上难免
的。所以：**不要拿本守卫的通过率当解环进度。**

## 白名单

存量违规全部写死在下面的 ``_EXEMPT`` 里，**故意不放配置文件**：加一条豁免必须改
代码、走 review，而不是偷偷加一行配置。

白名单是**棘轮**——多一条会红（新违规），少一条也会红（还完债忘了删）。所以还完
一对依赖，必须把对应的行删掉，红了就是提醒你删。

粒度是 (发起方模块, 被摸的 repository 模块)，不到类名。理由：同一个文件从同一个
领域的 repository 里多拿一个类，架构上还是同一处违规，不值得再走一次 review。
"""

import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "app" / "domain"

# ---------------------------------------------------------------------------
# 存量豁免。格式：(发起方模块, 被 import 的 repository 模块)
#
# 还债进度见 docs/topics/领域包解环.md。删行的正确姿势：把 service 改成调对方的
# service，然后删掉这里对应的行，跑本测试确认不红。
# ---------------------------------------------------------------------------
_EXEMPT: frozenset[tuple[str, str]] = frozenset(
    {
        # --- agent_credential --- (plain get + the epoch lives in
        # project.settings; no project/topic *service* exposes those reads
        # without dragging their full DI graph into credential minting)
        ("app.domain.agent_credential.services", "app.domain.project.repositories"),
        ("app.domain.agent_credential.services", "app.domain.topic.repositories"),
        # --- agent ---
        ("app.domain.agent.chat", "app.domain.block.repositories"),
        ("app.domain.agent.chat", "app.domain.milestone.repositories"),
        ("app.domain.agent.chat", "app.domain.project.repositories"),
        ("app.domain.agent.chat", "app.domain.review.repositories"),
        ("app.domain.agent.chat", "app.domain.team.repositories"),
        ("app.domain.agent.chat", "app.domain.topic.repositories"),
        ("app.domain.agent.chat", "app.domain.usage.repositories"),
        ("app.domain.agent.github_app", "app.domain.project.repositories"),
        ("app.domain.agent.sandbox_notices", "app.domain.block.repositories"),
        ("app.domain.agent.sandbox_notices", "app.domain.topic.repositories"),
        # Same shape, same reason as the two notice modules above and below:
        # posting one system event into a topic's timeline needs the block row
        # and the topic it hangs off, and `app.domain.block` has no service at
        # all — only models, schemas and repositories. There is nothing to call.
        ("app.domain.agent.snapshot_notices", "app.domain.block.repositories"),
        ("app.domain.agent.snapshot_notices", "app.domain.topic.repositories"),
        # --- answers / comments / discussion / groups ---
        ("app.domain.answers.services", "app.domain.user.repositories"),
        ("app.domain.answers.services", "app.domain.questions.repositories"),
        ("app.domain.comments.services", "app.domain.user.repositories"),
        ("app.domain.discussion.services", "app.domain.user.repositories"),
        ("app.domain.groups.services", "app.domain.user.repositories"),
        # --- conclusion ---
        # 这个领域在本工作区的 base 里还不存在（沙箱同步不了上游），两条是照 CI 在
        # 更新的 main 上报的原样入账的存量债，不是本轮新欠的。副作用：在缺 conclusion
        # 的旧 base 上跑，棘轮会把这两行报成"陈行"——那是 checkout 落后，不是债还完了。
        ("app.domain.conclusion.services", "app.domain.block.repositories"),
        ("app.domain.conclusion.services", "app.domain.topic.repositories"),
        # --- alert ---
        ("app.domain.alert.services", "app.domain.block.repositories"),
        ("app.domain.alert.services", "app.domain.project.repositories"),
        # --- dashboard（读模型，横跨 7 个领域聚合，单独还） ---
        ("app.domain.dashboard.services", "app.domain.alert.repositories"),
        ("app.domain.dashboard.services", "app.domain.membership.repositories"),
        ("app.domain.dashboard.services", "app.domain.milestone.repositories"),
        ("app.domain.dashboard.services", "app.domain.project.repositories"),
        ("app.domain.dashboard.services", "app.domain.space.repositories"),
        ("app.domain.dashboard.services", "app.domain.topic.repositories"),
        ("app.domain.dashboard.services", "app.domain.user.repositories"),
        # --- identity / knowledge / machine / membership / milestone / oauth ---
        ("app.domain.identity.services", "app.domain.user.repositories"),
        ("app.domain.knowledge.services", "app.domain.team.repositories"),
        ("app.domain.knowledge.services", "app.domain.user.repositories"),
        ("app.domain.machine.services", "app.domain.project.repositories"),
        ("app.domain.membership.services", "app.domain.project.repositories"),
        ("app.domain.milestone.services", "app.domain.project.repositories"),
        ("app.domain.oauth.services", "app.domain.user.repositories"),
        # --- project ---
        ("app.domain.project.services", "app.domain.topic.repositories"),
        ("app.domain.project.services", "app.domain.usage.repositories"),
        ("app.domain.project.services", "app.domain.user.repositories"),
        # --- questions ---
        ("app.domain.questions.services", "app.domain.user.repositories"),
        ("app.domain.questions.services", "app.domain.answers.repositories"),
        # --- review / scheduler ---
        # review.archive / review.gate_sweep 是本分支挂起期间从 main 进来的
        # （#286 闸门孤儿清扫等），不是本轮新欠的债，按存量入账。
        ("app.domain.review.archive", "app.domain.block.repositories"),
        ("app.domain.review.gate_sweep", "app.domain.block.repositories"),
        ("app.domain.review.gate_sweep", "app.domain.topic.repositories"),
        ("app.domain.review.services", "app.domain.block.repositories"),
        ("app.domain.review.services", "app.domain.membership.repositories"),
        ("app.domain.review.services", "app.domain.project.repositories"),
        ("app.domain.review.services", "app.domain.topic.repositories"),
        ("app.domain.scheduler.service", "app.domain.project.repositories"),
        # --- space ---
        ("app.domain.space.analytics_service", "app.domain.task.repositories"),
        ("app.domain.space.analytics_service", "app.domain.user.repositories"),
        ("app.domain.space.analytics_view_service", "app.domain.user.repositories"),
        (
            "app.domain.space.member_participating_service",
            "app.domain.user.repositories",
        ),
        ("app.domain.space.services", "app.domain.task.repositories"),
        # --- task ---
        ("app.domain.task.services", "app.domain.space.repositories"),
        ("app.domain.task.services", "app.domain.team.repositories"),
        ("app.domain.task.services", "app.domain.user.repositories"),
        ("app.domain.task.visibility_service", "app.domain.space.repositories"),
        ("app.domain.task.visibility_service", "app.domain.user.repositories"),
        # --- team / team_project ---
        ("app.domain.team.services", "app.domain.task.repositories"),
        ("app.domain.team_project.services", "app.domain.team.repositories"),
        ("app.domain.team_project.services", "app.domain.user.repositories"),
        # --- topic / topic_membership ---
        ("app.domain.topic.services", "app.domain.block.repositories"),
        ("app.domain.topic.services", "app.domain.project.repositories"),
        ("app.domain.topic_membership.services", "app.domain.identity.repositories"),
        ("app.domain.topic_membership.services", "app.domain.membership.repositories"),
        ("app.domain.topic_membership.services", "app.domain.project.repositories"),
        ("app.domain.topic_membership.services", "app.domain.topic.repositories"),
        ("app.domain.topic_membership.services", "app.domain.user.repositories"),
        # --- webhook / workspace ---
        ("app.domain.usage.subscription_ingest", "app.domain.project.repositories"),
        ("app.domain.webhook.service", "app.domain.block.repositories"),
        ("app.domain.workspace.dogfood_notices", "app.domain.block.repositories"),
        ("app.domain.workspace.dogfood_notices", "app.domain.topic.repositories"),
    }
)


def _is_repository_module(name: str) -> bool:
    """模块名的最后一段看起来是不是 repository 层。

    覆盖 ``repositories`` / ``repository`` / ``sql_repository`` /
    ``recruitment_repositories`` 这几种既有写法——不然改个名就能绕过去。
    """
    last = name.rsplit(".", 1)[-1]
    if last in ("repository", "repositories"):
        return True
    return last.endswith(("_repository", "_repositories"))


def _domain_of(module: str) -> str | None:
    """``app.domain.<pkg>.<...>`` → ``<pkg>``；不在领域包下则 None。"""
    parts = module.split(".")
    if len(parts) < 4 or parts[0] != "app" or parts[1] != "domain":
        return None
    return parts[2]


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parents[1])  # <root>/app/domain 的上两级
    return ".".join(rel.with_suffix("").parts)


def _resolve(node: ast.ImportFrom, current: str) -> str | None:
    """把相对 import 还原成绝对模块路径。"""
    if node.level == 0:
        return node.module
    base = current.split(".")[: -node.level]
    return ".".join(base + (node.module.split(".") if node.module else []))


def _scan(root: Path = DOMAIN_ROOT) -> list[tuple[str, str, int]]:
    """返回全部跨领域 repository import：(发起方模块, 目标模块, 行号)。

    ``root`` 指向 ``<某处>/app/domain``。默认扫真树；自检用它扫 tmp 里的假树。
    """
    found: list[tuple[str, str, int]] = []
    for py in sorted(root.rglob("*.py")):
        src_mod = _module_name(py, root)
        src_dom = _domain_of(src_mod)
        if src_dom is None:
            continue  # domain/common.py 这类顶层文件，不属于任何领域包
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = _resolve(node, src_mod)
                if not mod:
                    continue
                # 两种写法都要抓：
                #   from app.domain.x.repositories import Foo
                #   from app.domain.x import repositories
                targets = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for t in targets:
                if not _is_repository_module(t):
                    continue
                dst_dom = _domain_of(t)
                if dst_dom is None or dst_dom == src_dom:
                    continue
                found.append((src_mod, t, node.lineno))
    return found


def _violations(
    root: Path = DOMAIN_ROOT, exempt: frozenset[tuple[str, str]] = _EXEMPT
) -> list[tuple[str, str]]:
    """白名单之外的违规。"""
    return sorted({(src, dst) for src, dst, _ in _scan(root)} - exempt)


def _module_exists(root: Path, module: str) -> bool:
    """``app.domain.x.y`` 在这棵树里有没有对应的文件（模块或包）。"""
    base = root.joinpath(*module.split(".")[2:])
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def _stale_exemptions(
    root: Path = DOMAIN_ROOT, exempt: frozenset[tuple[str, str]] = _EXEMPT
) -> list[tuple[str, str]]:
    """白名单里已经没有对应违规的行。

    **发起方模块在这棵树里根本不存在的，不算陈行。** 棘轮这一半主张的是「债还完
    了，把行删掉」；文件都不在这个 checkout 里，这个主张就无从谈起——本仓的工作区
    经常落后于 main（见 CLAUDE.md），照 CI 在更新的 main 上报的违规入账时，那条
    豁免在旧 base 上必然找不到对应文件。不加这层判断，棘轮就会把「你的 checkout
    落后」误报成「这笔债还完了」，而正确的动作恰恰相反。

    代价说清楚：一个领域**真被整个删掉**时，它的豁免会留在白名单里没人提醒。
    换来的是不会把落后的 checkout 误判成还清了债——后者会诱导人删掉仍然有效的
    豁免，那是会让守卫漏判的方向，比多留几行严重。
    """
    actual = {(src, dst) for src, dst, _ in _scan(root)}
    return sorted(
        (src, dst) for src, dst in exempt - actual if _module_exists(root, src)
    )


def test_no_cross_domain_repository_imports() -> None:
    """跨领域 import 别人的 repository：白名单之外一律不许。"""
    violations = _violations()
    if violations:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in violations)
        pytest.fail(
            "发现跨领域 repository import：\n"
            f"{lines}\n\n"
            "正路是让本领域的 service 调对方的 service，"
            "而不是直接摸对方的 repository。\n"
            "确实拆不动的，把上面的行加进本文件的 _EXEMPT，并在 PR 里说明为什么。"
        )


def test_exemptions_are_all_still_needed() -> None:
    """白名单是棘轮：还完债必须把行删掉，不许留着虚胖。"""
    stale = _stale_exemptions()
    if stale:
        lines = "\n".join(f"  {src} → {dst}" for src, dst in stale)
        pytest.fail(
            "以下豁免已经没有对应的违规了，请从本文件的 _EXEMPT 里删掉：\n"
            f"{lines}\n\n"
            "（债还完了忘删白名单，下一个人就会以为这条依赖还在。）"
        )


# ---------------------------------------------------------------------------
# 自检：先证明这道守卫会红，再让它去判真树
#
# 抄 #264 的 .claude/scripts/check-repo-rules.sh --self-test。一道永远绿的守卫
# 和没有守卫是一回事，而且更糟——它会让人以为这条约束有人守着。上面两个测试当前
# 全绿，绿本身不构成"它能抓到东西"的证据；下面这组在 tmp 里搭假树，逐个形状证明
# 它会红、以及不该红的地方不红。
# ---------------------------------------------------------------------------


def _fake_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """在 tmp 里搭一棵 ``<tmp>/app/domain/...`` 的假领域树，返回 domain 根。

    路径形状必须和真树一致：``_module_name`` 是按 ``app/domain`` 上两级取相对
    路径算模块名的，假树跟着这个形状走，自检才测的是真代码路径。
    """
    domain = tmp_path / "app" / "domain"
    for rel, src in files.items():
        path = domain / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    return domain


#: 每一条都必须被抓到。key 是 pytest 的用例名，value 是 alpha/services.py 的内容。
_MUST_FAIL = {
    "from-x-repositories-import": "from app.domain.beta.repositories import BetaRepo\n",
    "from-x-import-repositories": "from app.domain.beta import repositories\n",
    "plain-import": "import app.domain.beta.repositories\n",
    "relative-import": "from ..beta.repositories import BetaRepo\n",
    "renamed-sql_repository": (
        "from app.domain.beta.sql_repository import SqlBetaRepo\n"
    ),
    "renamed-_repositories-suffix": (
        "from app.domain.beta.recruitment_repositories import RecruitRepo\n"
    ),
    "hidden-in-a-function-body": (
        "def f():\n"
        "    from app.domain.beta.repositories import BetaRepo\n"
        "    return BetaRepo\n"
    ),
}


@pytest.mark.parametrize("source", _MUST_FAIL.values(), ids=list(_MUST_FAIL))
def test_self_test_guard_goes_red_on(tmp_path: Path, source: str) -> None:
    """每种 import 写法都得抓到——改个名或者塞进函数体就绕过去的守卫不算守卫。"""
    root = _fake_tree(tmp_path, {"alpha/services.py": source})
    found = _violations(root, frozenset())
    assert found, f"没抓到：{source!r}"
    src, dst = found[0]
    assert src == "app.domain.alpha.services"
    assert dst.startswith("app.domain.beta.")


def test_self_test_whitelist_actually_suppresses(tmp_path: Path) -> None:
    """白名单能压住违规——不然存量违规会把守卫直接淹掉。"""
    root = _fake_tree(
        tmp_path, {"alpha/services.py": "from app.domain.beta.repositories import R\n"}
    )
    pair = ("app.domain.alpha.services", "app.domain.beta.repositories")
    assert _violations(root, frozenset()) == [pair]
    assert _violations(root, frozenset({pair})) == []


def test_self_test_ratchet_ignores_exemptions_for_absent_modules(
    tmp_path: Path,
) -> None:
    """发起方文件不在这棵树里 → 不算陈行；在、但 import 没了 → 算。

    两条一起测才有意义：只测前者会掩盖「这层判断把棘轮整个关掉了」。
    """
    root = _fake_tree(
        tmp_path, {"alpha/services.py": "from app.domain.beta.services import S\n"}
    )
    absent = ("app.domain.nosuch.services", "app.domain.beta.repositories")
    present = ("app.domain.alpha.services", "app.domain.beta.repositories")
    # checkout 落后：模块压根不存在，说明不了债还没还完
    assert _stale_exemptions(root, frozenset({absent})) == []
    # 真还完了：文件在，import 没了
    assert _stale_exemptions(root, frozenset({present})) == [present]


def test_self_test_ratchet_catches_a_stale_exemption(tmp_path: Path) -> None:
    """棘轮的另一半：债还完了白名单没删，也得红。"""
    root = _fake_tree(
        tmp_path, {"alpha/services.py": "from app.domain.beta import x\n"}
    )
    pair = ("app.domain.alpha.services", "app.domain.beta.repositories")
    assert _stale_exemptions(root, frozenset({pair})) == [pair]
    assert _stale_exemptions(root, frozenset()) == []


#: 每一条都**不许**被抓到——守卫判过头比判不到更难发现。
_MUST_PASS = {
    "same-domain-repository": "from app.domain.alpha.repositories import AlphaRepo\n",
    "cross-domain-service": "from app.domain.beta.services import BetaService\n",
    "cross-domain-models": "from app.domain.beta.models import Beta\n",
    "not-a-repository-module": "from app.domain.beta.wiring import beta_service\n",
    "outside-app-domain": "from app.core.errors import ValidationError\n",
    "relative-same-domain": "from .repositories import AlphaRepo\n",
}


@pytest.mark.parametrize("source", _MUST_PASS.values(), ids=list(_MUST_PASS))
def test_self_test_guard_stays_green_on(tmp_path: Path, source: str) -> None:
    root = _fake_tree(tmp_path, {"alpha/services.py": source})
    assert _violations(root, frozenset()) == [], f"误判：{source!r}"


def test_self_test_top_level_files_are_out_of_scope(tmp_path: Path) -> None:
    """``app/domain/common.py`` 这类不属于任何领域包的文件不在管辖范围内。

    这是设计取舍，不是漏网：它没有"自己的领域"，跨域的概念套不上去。
    """
    root = _fake_tree(
        tmp_path, {"common.py": "from app.domain.beta.repositories import BetaRepo\n"}
    )
    assert _violations(root, frozenset()) == []


def test_self_test_clean_tree_passes(tmp_path: Path) -> None:
    """干净的树必须绿——证明上面那些红不是因为扫描本身炸了。"""
    root = _fake_tree(
        tmp_path,
        {
            "alpha/services.py": "from app.domain.beta.services import BetaService\n",
            "alpha/repositories.py": "class AlphaRepo: ...\n",
            "beta/services.py": "from .repositories import BetaRepo\n",
            "beta/repositories.py": "class BetaRepo: ...\n",
        },
    )
    assert _violations(root, frozenset()) == []
