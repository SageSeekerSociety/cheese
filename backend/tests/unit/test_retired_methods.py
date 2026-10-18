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

表里既是「删的是哪几个」的记录，也是断言本身：把其中任何一个加回类上，对应的用例
就红。类还在、导入得到由这几条 import 保证——导入失败时整个文件都收集不起来。
"""

import pytest

from app.core.metrics import MetricsRegistry
from app.domain.avatars.repositories import AvatarRepository
from app.domain.feedback.proposals import ProposalService
from app.domain.machine.microcloud import MicroCloudClient
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService

#: 类 → 已经删掉的方法名。
RETIRED = {
    MicroCloudClient: ("suspend_machine", "resume_machine"),
    TaskPdfDraftService: ("generate_task_payload_from_pdf",),
    MetricsRegistry: ("histograms_named",),
    ProposalService: ("_is_dismissed",),
    AvatarRepository: ("increment_usage",),
}

CASES = [
    pytest.param(owner, name, id=f"{owner.__name__}.{name}")
    for owner, names in RETIRED.items()
    for name in names
]


@pytest.mark.parametrize(("owner", "name"), CASES)
def test_the_retired_method_is_not_back(owner: type, name: str) -> None:
    assert not hasattr(owner, name), (
        f"{owner.__name__}.{name} 全仓零引用，已经删掉；要用请先接上调用方，"
        "再从这张表里移走。"
    )
