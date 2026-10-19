"""守卫：六个全仓零引用的方法已经删掉，不许再长回来（候选池第十批 Y2）。

删之前每个都在全仓（含测试、文档、配置）用 `git grep -w` 核过一次，只有定义行一个
命中：

- `MicroCloudClient.suspend_machine` / `resume_machine`：机器回收走
  `DELETE /machine/{id}`；
- `TaskPdfDraftService.generate_task_payload_from_pdf`：只是复数方法 `max_tasks=1`
  的包装；
- `MetricsRegistry.histograms_named`：没有读者；
- `ProposalService._is_dismissed`：单条查询版，实际用的是集合版 `_dismissed`；
- `AvatarRepository.increment_usage`：`usage_count` 字段保留，但写它的调用方并不存在。

两道断言，各挡一种回流：

1. **类上取不到。** `assertFalse(hasattr(类, 名字))`，挡住普通的重写方法。
2. **源码里没有这个名字。** `hasattr(类, …)` 看不见挂在实例上的同名方法（`__init__`
   里的 `self.increment_usage = …`），也看不见类体里的别名赋值，所以再读一遍定义
   模块的 AST，把类体绑定的名字和方法体里 `self.<名字> =` 的赋值都算回流。这一点
   正对着 `test_dead_module_functions.py` 里「读源码的 AST，不走 hasattr」的理由。

下面两条自测证明第 2 道的收集器两个方向都会动：造一份带回流名字的类，它要收集到；
造一份干净的类，它不能凭空多出名字。

候选池第二十二批戊又补进 `check_run_names`：`GitHubPrClient` 声明它，
`HttpxGitHubPrClient` 与 Forgejo 的实现类 `ForgejoClient` 各实现一份，全仓零调用
——docstring 提到的 #468 轮询器早改读 `check_state` / `list_check_runs`。协议本身
也进这张表，所以把方法加回协议声明就会红。
"""

import ast
from pathlib import Path

import pytest

import app
from app.core.metrics import MetricsRegistry
from app.domain.avatars.repositories import AvatarRepository
from app.domain.feedback.proposals import ProposalService
from app.domain.machine.microcloud import MicroCloudClient
from app.domain.review.forgejo_pr import ForgejoClient
from app.domain.review.github_pr.httpx_client import HttpxGitHubPrClient
from app.domain.review.github_pr.protocol import GitHubPrClient
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService

APP_ROOT = Path(app.__file__).resolve().parent

#: 模块 → 类 → 已经删掉的方法名。
RETIRED = {
    "app.core.metrics": {"MetricsRegistry": ("histograms_named",)},
    "app.domain.avatars.repositories": {"AvatarRepository": ("increment_usage",)},
    "app.domain.feedback.proposals": {"ProposalService": ("_is_dismissed",)},
    "app.domain.machine.microcloud": {
        "MicroCloudClient": ("suspend_machine", "resume_machine")
    },
    "app.domain.review.forgejo_pr": {"ForgejoClient": ("check_run_names",)},
    "app.domain.review.github_pr.httpx_client": {
        "HttpxGitHubPrClient": ("check_run_names",)
    },
    "app.domain.review.github_pr.protocol": {"GitHubPrClient": ("check_run_names",)},
    "app.domain.task.task_pdf_draft_service": {
        "TaskPdfDraftService": ("generate_task_payload_from_pdf",)
    },
}

#: 类对象，给第一道断言用。导入本身也钉住「类还在」。
CLASSES = {
    "MetricsRegistry": MetricsRegistry,
    "AvatarRepository": AvatarRepository,
    "ProposalService": ProposalService,
    "MicroCloudClient": MicroCloudClient,
    "ForgejoClient": ForgejoClient,
    "HttpxGitHubPrClient": HttpxGitHubPrClient,
    "GitHubPrClient": GitHubPrClient,
    "TaskPdfDraftService": TaskPdfDraftService,
}

CASES = [
    pytest.param(module, owner, name, id=f"{owner}.{name}")
    for module, owners in RETIRED.items()
    for owner, names in owners.items()
    for name in names
]

CLASS_CASES = [
    pytest.param(owner, name, id=f"{owner}.{name}")
    for owners in RETIRED.values()
    for owner, names in owners.items()
    for name in names
]


def _source_of(module: str) -> str:
    return (APP_ROOT / Path(*module.split(".")[1:]).with_suffix(".py")).read_text(
        encoding="utf-8"
    )


def _bound_names(source: str, class_name: str) -> set[str]:
    """这个类上能取到的名字：类体绑定的，加上方法里 `self.<名字> =` 绑定的。"""
    tree = ast.parse(source)
    node = next(
        (
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.ClassDef) and item.name == class_name
        ),
        None,
    )
    if node is None:
        return set()

    names: set[str] = set()

    def add(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            names.add(target.attr)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add(element)

    for item in ast.walk(node):
        if isinstance(item, ast.Assign):
            for target in item.targets:
                add(target)
        elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
            add(item.target)
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in item.names
            )

    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)

    return names


@pytest.mark.parametrize(("owner", "name"), CLASS_CASES)
def test_the_retired_method_is_not_on_the_class(owner: str, name: str) -> None:
    assert not hasattr(CLASSES[owner], name), (
        f"{owner}.{name} 全仓零引用，已经删掉；要用请先接上调用方，再从这张表里移走。"
    )


@pytest.mark.parametrize(("module", "owner", "name"), CASES)
def test_the_retired_method_is_not_bound_by_the_source(
    module: str, owner: str, name: str
) -> None:
    assert name not in _bound_names(_source_of(module), owner), (
        f"{module} 的 {owner} 又把 {name} 绑回来了"
        "（方法、类属性、self. 赋值或导入都算）；"
        "要不要恢复它，先把调用方和这张表一起改。"
    )


def test_the_collector_finds_every_way_a_name_comes_back() -> None:
    source = """
class Thing:
    from somewhere import imported_name

    async def a_method(self):
        pass

    def __init__(self):
        self.an_instance_attribute = 0
"""
    assert {"imported_name", "a_method", "an_instance_attribute"} <= _bound_names(
        source, "Thing"
    )


def test_the_collector_does_not_invent_names() -> None:
    source = """
class Thing:
    def kept(self):
        pass
"""
    assert _bound_names(source, "Thing") == {"kept"}
