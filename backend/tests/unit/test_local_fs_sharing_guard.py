"""分享纪律：本机目录不参与共享。

分享出去的永远是成果本身，不是进入用户电脑的钥匙。这条不是能力而是纪律，所以它不靠
注释维持，靠这个测试：以后谁把授权目录接进一个可分享的东西里，这里会红。

容易做错的地方很具体——它们都不像是错事：

* 把目录授权做成一个「资源」，于是有了 id，于是可以像成员、附件、素材那样被引用。
  下一步就是 `attach(directory_id, task_id)`。
* 把授权挂在项目或团队上，让别的参与者也能用你的电脑。
* 把 `DirectoryGrant` 序列化进某个任务的响应里，让同项目的人看得见你机器上的绝对路径。

所以下面检查的四件事，对应上面四种姿势。（一）到（三）是源码级的不变量，（四）是那个
最容易被后人扩宽的**形状棘轮**：授权响应里每个字段都是刻意选的，多一个字段就该红。
"""

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.api.routes.local_dirs import _grant_view
from app.domain.local_fs.memory_repository import InMemoryLocalFsRepository
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import (
    DirectoryGrant,
    GrantMode,
    GrantScope,
)
from app.domain.local_fs.service import AuthorizeRequest, LocalDirectoryService

BACKEND = Path(__file__).resolve().parents[2]
ROUTES = BACKEND / "app" / "api" / "routes"
OWN_MODULE = ROUTES / "local_dirs.py"

# 路由装饰器的方法名。AST 里它们就是普通属性名，没有别的地方能告诉你哪些函数是端点。
_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

# 授权只能出现在主人的命名空间下。路径里出现下面任何一个词，就说明它变成了一个可以被
# 交给别人的东西。
_SHARING_WORDS = (
    "share",
    "public",
    "publish",
    "invite",
    "material",
    "attachment",
    "clone",
    "template",
)


# -- (一) 每个碰设备的端点都自己查一遍拥有关系 ---------------------------


def _route_functions(tree: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr in _METHODS:
                found.append(node)
                break
    return found


def test_every_directory_endpoint_checks_device_ownership():
    """平台不是执行点，但平台是「谁在问」的唯一判断处。一个忘了查拥有关系的端点，就是
    让别人通过猜 device_id 去读你机器上的路径。"""
    tree = ast.parse(OWN_MODULE.read_text(encoding="utf-8"))
    handlers = _route_functions(tree)
    assert handlers, "没有找到任何端点——路由的形状变了，这个守卫也就失效了"

    unchecked = []
    for handler in handlers:
        takes_device = any(a.arg == "device_id" for a in handler.args.args)
        if not takes_device:
            # 不接设备 id 的端点（审计列表）只能靠登录身份自己限定，这里只要求它
            # 真的把 user_id 传下去。
            source = ast.dump(handler)
            assert "_require_user" in source, f"{handler.name} 没有登录校验"
            continue
        source = ast.dump(handler)
        if "_require_owned_device" not in source:
            unchecked.append(handler.name)

    assert unchecked == [], (
        "这些端点碰了 device_id 却没有确认它属于调用者：" + ", ".join(unchecked)
    )


# -- (二) 授权不外泄到别的路由模块 ----------------------------------------


def test_no_other_route_module_touches_local_fs():
    """授权只能在它自己的路由里出现。一旦任务、素材、附件的响应里能带上授权，
    它就已经在共享了——只是还没有人把它叫「共享」。"""
    offenders = []
    for path in sorted(ROUTES.glob("*.py")):
        if path == OWN_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.domain.local_fs"
            ):
                offenders.append(f"{path.name}: {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.domain.local_fs"):
                        offenders.append(f"{path.name}: {alias.name}")

    assert offenders == [], (
        "这些路由模块碰了 local_fs——授权不该跟成果一起被分享出去："
        + ", ".join(offenders)
    )


# -- (三) 没有任何一条路径把授权和一个可分享的词放在一起 -------------------


def _route_paths(source: str) -> list[str]:
    tree = ast.parse(source)
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _METHODS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def test_no_endpoint_puts_a_directory_next_to_a_sharing_word():
    paths = _route_paths(OWN_MODULE.read_text(encoding="utf-8"))
    assert paths, "没有找到任何路径——路由的形状变了"

    bad = [p for p in paths if any(word in p.lower() for word in _SHARING_WORDS)]
    assert bad == [], (
        "授权出现了在可分享的路径下——分享出去的应该是成果，不是钥匙：" + ", ".join(bad)
    )


def test_every_directory_endpoint_is_scoped_to_the_callers_own_machine():
    """`/my/devices/{device_id}/...` 本身就是保护：没有「某个项目的目录」这种地址，
    所以也不存在把目录交给项目去用的写法。"""
    paths = _route_paths(OWN_MODULE.read_text(encoding="utf-8"))
    outside = [p for p in paths if "/my/" not in p]
    assert outside == [], (
        "这些端点不在「我的电脑」下，所以它们可以用别的项目的身份去碰别人的磁盘："
        + ", ".join(outside)
    )


# -- (四) 形状棘轮：授权响应里多一个字段就该红 -------------------------


_GRANT_FIELDS = frozenset(
    {
        "id",
        "device_id",
        "path",
        "platform",
        "mode",
        "scope",
        "project_id",
        "created_at",
        "revoked_at",
    }
)


def test_the_grant_response_carries_no_field_that_could_be_handed_out():
    """每个字段都是刻意选的。多一个 `share_token`、`signature`、`url` 就会蓝——
    那时候值得停下来想一下：这个东西能不能被发给别人。"""
    grant = DirectoryGrant(
        id=uuid.uuid4(),
        device_id="device-a1",
        path="/home/alice/MyDocs",
        key="/home/alice/mydocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.PROJECT,
        owner_user_id=7,
        created_at=datetime.now(UTC),
    )

    assert set(_grant_view(grant)) == _GRANT_FIELDS


# -- (五) 审计也是每条设备、每个主人各看各的 -------------------------


async def test_one_persons_access_log_is_not_another_persons():
    """审计里有绝对路径。它只属于那台机器的主人。"""
    repo = InMemoryLocalFsRepository()
    service = LocalDirectoryService(repo)
    await service.grant_directory(
        device_id="device-a1",
        owner_user_id=7,
        path="/home/alice/MyDocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
    )
    await service.authorize(
        AuthorizeRequest(
            device_id="device-a1",
            path="/home/alice/MyDocs/grades.csv",
            platform=Platform.LINUX,
            needed=GrantMode.READ,
            owner_user_id=7,
        )
    )

    assert len(await service.list_access(7)) == 1
    assert await service.list_access(8) == []
