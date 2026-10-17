"""两块凭据家族共用同一个密钥和同一种签名构造，测试把它钉在一处。

``app.core.webhook_auth`` 和 ``app.core.sandbox_auth`` 签的是不同的 claims；把它们
分开的是各自的签名域和前缀，不是算法。算法各留一份的代价是：改了一边，另一边不跟
着变，而两个模块都不会报错——凭据只是悄悄分成两套。

所以这里钉两件事：两个模块铸出的 token，签名正好是 ``sign_body(密钥, body)`` 的
输出（行为上证明它们真的经过那一个算法）；两个模块里都不再有第二份签名函数（结构
上证明它没有悄悄回来）。

故意违规：把 ``webhook_auth`` 的本地 ``_sign`` 贴回去并改掉一个字节，第一条红；
贴回一份逐字相同的副本，第二条红。
"""

import ast
import importlib
import inspect

from app.core import hmac_token, sandbox_auth, webhook_auth

SIGNER_MODULES = ("app.core.webhook_auth", "app.core.sandbox_auth")


def _the_signature_of(token: str) -> tuple[str, str]:
    body, _, signature = token.partition(".")
    return body, signature


def test_a_webhook_token_is_signed_by_the_shared_helper():
    body, signature = _the_signature_of(
        webhook_auth.mint_webhook_token(project_id="p", topic_id="t", version=1)
    )
    assert signature == hmac_token.sign_body(sandbox_auth.SANDBOX_TOKEN, body)


def test_a_sandbox_token_is_signed_by_the_shared_helper():
    body, signature = _the_signature_of(
        sandbox_auth.mint_scoped_token(project_id="p", topic_id="t")
    )
    assert signature == hmac_token.sign_body(sandbox_auth.SANDBOX_TOKEN, body)


def test_both_modules_take_the_one_signing_helper():
    assert webhook_auth.sign_body is sandbox_auth.sign_body is hmac_token.sign_body


def test_neither_module_keeps_its_own_signing_function():
    for name in SIGNER_MODULES:
        tree = ast.parse(inspect.getsource(importlib.import_module(name)))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_sign" not in defined, f"{name} defines its own signing function"
