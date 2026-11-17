"""五个运行环境：出题人选的键 → 评测主机上起的镜像（方案 §运行环境镜像）。

键和题目一侧收的键（``app.domain.task.test_suite.RUNTIME_KEYS``）一一对应，一致性由
``tests/unit/test_judge_runtimes.py`` 守着：出题表单能选的每一种，这里都要能跑。

镜像由流水线从 ``backend/sandbox/judge/Dockerfile.<name>`` 构建（``build.yml`` 的
``build-judge``），基础镜像在 Dockerfile 里钉死版本，每个镜像都带 GNU time 和 uid 1000
的用户。评测主机按这里的名字取镜像；主机上先拉好（``docs/manual/dev/judge.md``），
评测时不联网拉。
"""

from dataclasses import dataclass

#: 流水线发布镜像的地方（``build.yml``：``ghcr.io/<仓库>/judge-<name>``）。
REGISTRY = "ghcr.io/sageseekersociety/cheese"
#: 评测主机用的那一版：主干最新构建出的镜像。
TAG = "main"


@dataclass(frozen=True)
class Runtime:
    key: str
    #: ``backend/sandbox/judge/Dockerfile.<name>`` 的 ``<name>``，也是镜像名的后缀。
    name: str

    @property
    def image(self) -> str:
        return f"{REGISTRY}/judge-{self.name}:{TAG}"


RUNTIMES: dict[str, Runtime] = {
    runtime.key: runtime
    for runtime in (
        Runtime("python3.12", "python"),
        Runtime("cpp17", "cpp"),
        Runtime("java21", "java"),
        Runtime("node22", "node"),
        Runtime("go1.23", "go"),
    )
}


def image_of(key: str) -> str | None:
    """The image a runtime key runs in; None for a key nobody can pick."""
    runtime = RUNTIMES.get(key)
    return runtime.image if runtime is not None else None
