"""门面守卫：``app.domain.oauth.services`` 拆文件之后，行为和公开面一个都不能变。

这次拆分把 ``services.py``（897 行）变成一个包 ``app/domain/oauth/services/``，三块
搬进了包里的子模块：

- ``providers.py`` —— ``OAuthProvider`` 协议、三家实现、``PROVIDER_CLASSES`` /
  ``LINK_ONLY_PROVIDERS``；
- ``tokens.py`` —— ``seal_oauth_token`` / ``open_oauth_token``、
  ``TOKEN_UNAVAILABLE_*``，以及 ``_decrypt_stored_token`` /
  ``get_github_user_token*`` / ``_refresh_and_persist_token`` /
  ``_current_token_if_fresh``；
- ``connections.py`` —— 连接的查、建、改 token、列、删，加 ``_connection_to_dict``。

它们是同一个包里的子模块，不是三个同级模块：同级模块互相 import 会在 C3
（``app.domain`` 下的同级包不许成环）上多出环，而子模块 import 的是父包这个节点，
「子指向祖先」那条边契约不算。形状和 ``app/domain/feedback/repositories/`` 一样。

留在包的门面（``__init__.py``）里的是「这次部署启用什么、凭据从哪读、授权怎么走」：
类上的 ``_initialize`` / ``_get_provider_config`` / ``get_providers_config`` /
``get_provider`` / ``generate_authorization_url`` / ``handle_callback`` 原样没动
（它们管 ``self._providers`` / ``self._initialized`` 这两个门面自己的字段），末尾三个
按 handle 取身份的模块级函数也没动。

这道测试不连数据库，钉住四件事：

1. 旧路径仍答得出原来那一组名字，而且是**同一批对象**（``GitHubProvider``、
   ``seal_oauth_token``、``TOKEN_UNAVAILABLE_*``），不是各留一份；
2. 每个名字现在住在哪个模块里，一个不差，而且**没有哪个实现写了两份** —— 这是
   「谁的职责归谁」的可执行版；
3. ``__init__`` 没动；
4. **拆的是文件，不是行为**：搬走的 11 个方法在类上恰好一句转发；留下的 7 个方法
   体里没有转发（判断还在类里）。
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import textwrap

import pytest

from app.core.config import settings
from app.domain.oauth import (
    services as oauth_services,
)
from app.domain.oauth.services import (
    connections,
    providers,
    tokens,
)

#: 搬运之前那份 services.py 里 OAuthService 的类体，按定义先后抄的。
OAUTH_SERVICE_METHODS = [
    "__init__",
    "_initialize",
    "_get_provider_config",
    "get_providers_config",
    "get_provider",
    "generate_authorization_url",
    "handle_callback",
    "get_connection_by_provider",
    "create_connection",
    "update_connection_tokens",
    "_decrypt_stored_token",
    "get_github_user_token",
    "get_github_user_token_with_reason",
    "_refresh_and_persist_token",
    "_current_token_if_fresh",
    "list_user_connections",
    "delete_connection",
    "_connection_to_dict",
]

#: 留在类里的：注册表与授权回调，方法体还是它们自己的。
KEPT_IN_THE_CLASS = {
    "__init__",
    "_initialize",
    "_get_provider_config",
    "get_providers_config",
    "get_provider",
    "generate_authorization_url",
    "handle_callback",
}

#: 方法体搬去的那两个子模块；`_connection_to_dict` 不拿服务状态，转发时不传 svc。
SUBMODULES = {"tokens": tokens, "connections": connections}

#: 每个名字现在该住在哪里 —— 模块搬错地方这条就红。
HOME = {
    providers: [
        "OAuthUserInfo",
        "OAuthProviderConfig",
        "OAuthProvider",
        "GitHubProvider",
        "GoogleProvider",
        "RUCProvider",
        "PROVIDER_CLASSES",
        "LINK_ONLY_PROVIDERS",
    ],
    tokens: [
        "seal_oauth_token",
        "open_oauth_token",
        "TOKEN_UNAVAILABLE_NOT_CONNECTED",
        "TOKEN_UNAVAILABLE_UNDECRYPTABLE",
        "TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH",
        "TOKEN_UNAVAILABLE_REFRESH_FAILED",
        "TOKEN_UNAVAILABLE_PROVIDER_NOT_CONFIGURED",
    ],
    oauth_services: [
        "OAuthService",
        "OAuthConnectionRepository",
        "get_github_profile_for_handle",
    ],
}

#: 住在 ``connections.py`` 里、门面**不**再导出的那六个函数：调用方走类上的转发，或者
#: ``oauth_services.connections.<name>``。它们没有第二份可比，所以就住在这一个模块里。
IN_CONNECTIONS_ONLY = [
    "get_connection_by_provider",
    "create_connection",
    "update_connection_tokens",
    "list_user_connections",
    "delete_connection",
    "_connection_to_dict",
]

#: 门面再导出的名字：调用点和单测按老路径取的就是这些。少一个就是 ImportError。
REEXPORTED_FROM_PROVIDERS = [
    "OAuthUserInfo",
    "OAuthProviderConfig",
    "OAuthProvider",
    "GitHubProvider",
    "GoogleProvider",
    "RUCProvider",
    "PROVIDER_CLASSES",
    "LINK_ONLY_PROVIDERS",
]
REEXPORTED_FROM_TOKENS = [
    "seal_oauth_token",
    "open_oauth_token",
    "TOKEN_UNAVAILABLE_NOT_CONNECTED",
    "TOKEN_UNAVAILABLE_UNDECRYPTABLE",
    "TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH",
    "TOKEN_UNAVAILABLE_REFRESH_FAILED",
    "TOKEN_UNAVAILABLE_PROVIDER_NOT_CONFIGURED",
]


@pytest.mark.parametrize("name", REEXPORTED_FROM_PROVIDERS)
def test_the_old_path_answers_the_provider_names_with_the_same_objects(
    name: str,
) -> None:
    """`from app.domain.oauth.services import GitHubProvider` 取到的还是那一个。"""
    assert getattr(oauth_services, name) is getattr(providers, name)


@pytest.mark.parametrize("name", REEXPORTED_FROM_TOKENS)
def test_the_old_path_answers_the_token_names_with_the_same_objects(name: str) -> None:
    """`seal_oauth_token` / `open_oauth_token` / `TOKEN_UNAVAILABLE_*` 同理。

    `TOKEN_UNAVAILABLE_*` 这组字符串是给调用方比对的，所以要的是**同一个对象**，不是各留一份相等的字面量。

    这条挡不住的是「抄一份、文本碰巧一字不差」：CPython 会把这种短字符串驻留，两份同
    文本的字面量恰好也是同一个对象。它挡得住的是抄一份、文本改过的那种 —— 那才会让
    两个导入路径给出两个答案，也正是拆分最容易留下的分叉。
    """
    assert getattr(oauth_services, name) is getattr(tokens, name)


def test_the_facade_still_reads_the_same_settings_object() -> None:
    """注册表读的仍是 `app.core.config.settings` 那一个对象。

    单测按 `app.domain.oauth.services.settings` 打了 15 处补丁，读 settings 的代码
    （`_initialize` / `_get_provider_config`）留在本模块，所以补丁打的正是它们读的
    那个名字；换成 `from app.core.config import settings as s` 这类写法，补丁就成了
    静默的空操作，而所有 `oauth_enabled_providers` 相关的测试会以别的方式红。
    """
    assert oauth_services.settings is settings


def test_the_moved_modules_live_inside_the_services_package() -> None:
    """三块搬进的是 `app.domain.oauth.services` 这个**包**，不是三个同级模块。

    同级模块互相 import 会在 C3（`app.domain` 下的同级包不许成环）上多出环；放进
    同一个包里，子模块 import 的是父包这个节点，那条边不算环。谁把 `connections.py`
    挪回 `app/domain/oauth/` 下面，这条先红，架构检查跟着红。
    """
    assert hasattr(oauth_services, "__path__"), "门面已经不是包了"
    assert connections.__name__ == "app.domain.oauth.services.connections"
    assert tokens.__name__ == "app.domain.oauth.services.tokens"
    assert providers.__name__ == "app.domain.oauth.services.providers"


@pytest.mark.parametrize(
    "module,names", list(HOME.items()), ids=["providers", "tokens", "services"]
)
def test_each_name_lives_in_the_module_that_owns_it(module, names: list[str]) -> None:
    """每个名字只在一个模块里定义，门面里那份是同一个对象。"""
    for name in names:
        assert hasattr(module, name), f"{module.__name__} 里没有 {name}"
        assert getattr(module, name) is getattr(oauth_services, name), (
            f"{name} 在两个模块里是两份东西"
        )


def test_the_connection_functions_live_in_connections_alone() -> None:
    """`connections.py` 那六个函数只在子模块里有一份，门面不再导出它们。

    上面 `HOME` 只能管「两处是同一个对象」的名字；连接这一族是另一回事：调用方走类上
    的转发，或者 `oauth_services.connections.<name>`，门面里根本没有同名的一份可比。
    谁把它们抄回门面（或搬去别的子模块），这条红 —— 两份实现迟早分叉。
    """
    for name in IN_CONNECTIONS_ONLY:
        assert hasattr(connections, name), f"connections.py 里没有 {name}"
        assert not hasattr(oauth_services, name), f"{name} 在门面里另有一份"


def test_no_module_level_function_is_defined_twice() -> None:
    """门面和三个子模块加起来，没有哪个模块级函数写了两份。

    「一份判据只有一处实现」是这次拆分的目的，所以拿这四份文件的模块级 `def` 名做一个
    集合对账：谁把 `create_connection` 抄回门面（子模块那份还在），或者把某个搬走的
    实现又贴回另一个子模块，这里就有一对重名。`_decrypt_stored_token` 这类只在子模块
    内部被自己人叫的私有函数不受影响 —— 它只定义了一次。

    （类上的方法不算：转发是一句转发，判据在子模块里，两者的名字本来就该一样。）
    """
    submodules = (providers, tokens, connections)
    defined: dict[str, str] = {}
    for module in (oauth_services, *submodules):
        source = pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assert node.name not in defined, (
                f"{node.name} 在 {defined[node.name]} 和 {module.__name__} 里各有一份"
            )
            defined[node.name] = module.__name__

    # 对账本身要有内容：这四个文件里确实有一批模块级函数，别把空集当通过。
    assert len(defined) > 10, f"只数到 {len(defined)} 个模块级函数，这四个文件不对"


def test_the_service_keeps_all_18_methods() -> None:
    """18 个方法一个不少，而且都是类自己定义的（不是 `__getattr__` 转发）。"""
    assert len(OAUTH_SERVICE_METHODS) == 18
    assert len(KEPT_IN_THE_CLASS) == 7
    own = set(vars(oauth_services.OAuthService))
    assert [n for n in OAUTH_SERVICE_METHODS if n not in own] == []
    assert "__getattr__" not in own


def test_init_still_builds_the_repository_and_the_registry() -> None:
    """拆分没动 __init__：三个属性仍在这里赋值，赋的什么也没换。"""
    init = ast.parse(
        textwrap.dedent(inspect.getsource(oauth_services.OAuthService.__init__))
    ).body[0]
    assert [a.arg for a in init.args.args] == ["self", "repo"]

    assigned: dict[str, str] = {}
    for node in ast.walk(init):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    assigned[target.attr] = ast.unparse(node.value)
    assert assigned == {
        "_repo": "repo",
        "_providers": "{}",
        "_initialized": "False",
    }


@pytest.mark.parametrize(
    "name", [n for n in OAUTH_SERVICE_METHODS if n in KEPT_IN_THE_CLASS]
)
def test_the_kept_methods_kept_their_bodies(name: str) -> None:
    """留在类里的这 7 个不是转发：它们的判断还在类里，搬走了这条就红。

    `handle_callback` 与 `get_provider` 是授权流程本身，`_get_provider_config` 是
    「凭据从哪些 settings 读」—— 三件都只在这个模块里有一份。
    """
    body = _method_body(oauth_services.OAuthService, name)
    assert not (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(_called_module(body[0]), ast.Name)
    ), f"{name} 成了转发，它的实现应该留在这个模块里"


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in OAUTH_SERVICE_METHODS
        if n not in KEPT_IN_THE_CLASS and n != "__init__"
    ],
)
def test_every_moved_method_is_one_forward_to_its_sibling_function(name: str) -> None:
    """方法体恰好一句转发，转给子模块里同名、首参为 `svc` 的那个函数。

    转发本身也是行为：这条要求每个参数都按同一个名字、同一个位置原样送出去，
    一个不漏、一个不多 —— 少送一个（`provider_id` 丢了、`field` 丢了）就是静默
    改行为。另外要求**经 `self` 走**：`test_oauth_service.py` 按
    `patch.object(svc, "_refresh_and_persist_token")` 打在实例上，类里改成直接调
    子模块函数，那条补丁就成了空操作而测试仍然绿。
    """
    body = _method_body(oauth_services.OAuthService, name)
    assert len(body) == 1, f"{name} 的方法体不止一句，实现被抄回来了"
    statement = body[0]
    assert isinstance(statement, ast.Return), f"{name} 不是转发"

    call = statement.value
    if isinstance(call, ast.Await):
        call = call.value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    target_module = call.func.value
    assert isinstance(target_module, ast.Name) and target_module.id in SUBMODULES
    assert call.func.attr == name, f"{name} 转发去了另一个名字 {call.func.attr}"

    forwarded = getattr(SUBMODULES[target_module.id], name)
    parameters = list(inspect.signature(forwarded).parameters)
    method_parameters = _method_parameters(oauth_services.OAuthService, name)

    # `_connection_to_dict` 只拿一行数据，不看服务状态，所以不传 svc。
    takes_service = name != "_connection_to_dict"
    expected = (["self"] if takes_service else []) + method_parameters
    # 位置形参按位置送，keyword-only 形参按关键字送 —— 两种都收在一个表里比对。
    names = [a.id if isinstance(a, ast.Name) else "?" for a in call.args]
    for keyword in call.keywords:
        assert keyword.arg is not None, f"{name} 转发时用了 **kwargs"
        assert isinstance(keyword.value, ast.Name), (
            f"{name} 转发的 {keyword.arg} 不是变量"
        )
        names.append(keyword.arg)
        assert keyword.value.id == keyword.arg, (
            f"{name} 把 {keyword.value.id} 送给了 {keyword.arg}"
        )
    assert names == expected, (
        f"{name} 的转发表 {names} 与它自己的形参 {expected} 对不上"
    )
    if takes_service:
        assert parameters[0] == "svc"
    assert [n for n in parameters if n != "svc"] == method_parameters, (
        f"{name} 的转发表与 {target_module.id}.{name} 的形参对不上"
    )


def _method_body(service: type, name: str) -> list[ast.stmt]:
    method = getattr(service, name)
    body = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0].body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # 方法自己的 docstring 跟着方法走了
    return body


def _called_module(statement: ast.stmt) -> ast.expr | None:
    """转发调用的那一边（`tokens` / `connections`），不是转发就 None。"""
    if not isinstance(statement, ast.Return):
        return None
    call = statement.value
    if isinstance(call, ast.Await):
        call = call.value
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
        return call.func.value
    return None


def _method_parameters(service: type, name: str) -> list[str]:
    """方法自己声明的形参名，不含 `self`。"""
    declaration = ast.parse(
        textwrap.dedent(inspect.getsource(getattr(service, name)))
    ).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = declaration.args
    return [
        a.arg
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if a.arg != "self"
    ]
