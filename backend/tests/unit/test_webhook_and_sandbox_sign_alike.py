"""两块凭据家族共用同一个密钥和同一种签名构造，测试把它钉在一处。

``app.core.webhook_auth`` 和 ``app.core.sandbox_auth`` 签的是不同的 claims；把它们
分开的是各自的签名域和前缀，不是算法。算法各留一份的代价是：改了一边，另一边不跟
着变，而两个模块都不会报错——凭据只是悄悄分成两套。

所以这里钉的是：两个模块铸出的 token，签名正好是 ``sign_body(密钥, body)`` 的
输出——行为上证明它们真的经过那一个算法。
"""

from app.core import hmac_token, sandbox_auth, webhook_auth


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
