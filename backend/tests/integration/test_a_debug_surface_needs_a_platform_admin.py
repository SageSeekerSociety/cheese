"""调试面和监控面只对平台管理员开放。

`/debug/turns`、`/metrics`、`/health/detailed` 各自都在描述平台的内部状态：谁在跑、
哪一步失败了、每个路由被调了多少次、哪个依赖挂住了。从公开地址能读到它们，等于把
一块「你现在哪里坏了」的实时面板挂在互联网上。

公开的那几条是被别的东西读着的，不能一起收紧：`/healthz`、`/health`、`/version` 是
部署探针和前端版本角标，`/readyz` 是依赖级的就绪门 —— 它们都没有凭据可出示，而且
`/readyz` 挂在 503 上时，这份健康清单还得跟着响应体一起发出去。

拒绝的形态是 403，和 `/admin/*` 一样：没有身份和身份不对在这里是同一个答案。
"""

import pytest

from app.core.config import settings
from tests.integration.conftest import session_auth_headers

ADMIN = "surface-admin"
STRANGER = "surface-stranger"

# 要过门的。三条都要平台管理员。
GATED = ("/debug/turns", "/metrics", "/health/detailed")

# 不给凭据也要能读到的。`/readyz` 单独看：它可以是 503（依赖没起来），只要不是
# 被门拦下的 403 就说明这一条还是公开的。
PUBLIC_ALWAYS_200 = ("/healthz", "/health", "/version")


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


@pytest.mark.parametrize("path", GATED)
def test_without_a_credential_the_gated_surfaces_say_nothing(client, as_admin, path):
    """没带凭据 —— 和路径不存在时能问出的一样少。"""
    response = client.get(path)

    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("path", GATED)
def test_a_signed_in_stranger_gets_the_same_refusal(client, as_admin, path):
    """有身份、但不是平台管理员 —— 答案不变。

    这一条和上一条分开，是因为「谁都能读」和「登录了就能读」是两种漏法，实现里
    也是两道判断（`refuse_management_action` 与 `require_admin`）。
    """
    response = client.get(path, headers=session_auth_headers(STRANGER))

    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("path", GATED)
def test_a_platform_admin_reads_them(client, as_admin, path):
    """本人 —— 三条都要真的读到东西，不是「过了门但里面是空的」。"""
    response = client.get(path, headers=session_auth_headers(as_admin))

    assert response.status_code == 200, response.text
    assert response.json()


def test_the_health_report_is_still_there_for_an_admin(client, as_admin):
    """门后还是那份清单，不是一个只剩 status 的壳。

    `/readyz` 的就绪判据和后台面板的健康分栏都读同一份报告，所以这里连判据一起钉：
    数据库那一项要有结论。
    """
    body = client.get("/health/detailed", headers=session_auth_headers(as_admin)).json()

    assert "status" in body
    assert "database" in body["checks"]


@pytest.mark.parametrize("path", PUBLIC_ALWAYS_200)
def test_the_deployment_probes_stay_open(client, as_admin, path):
    """部署探针和版本角标没有凭据可出示，收紧它们等于把部署拦住。"""
    assert client.get(path).status_code == 200


def test_readiness_answers_without_a_credential(client, as_admin):
    """`/readyz` 公开：可以是 503（依赖挂了），但不能是被门拦下。"""
    response = client.get("/readyz")

    assert response.status_code in (200, 503), response.text


def test_readiness_does_not_publish_a_dependency_error(client, monkeypatch):
    """503 体说哪一项没就绪，不带依赖的报错原文 —— 那段文字可能有内网地址和用户名，
    它留在要管理员的 `/health/detailed` 后面。"""
    from app.api.routes import health

    async def _down():
        return {"status": "down", "error": "no route to db.internal:5432 as cheesex"}

    monkeypatch.setattr(health, "_check_database", _down)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert "db.internal" not in response.text
    body = response.json()
    assert "database" in body["unready"]
    assert body["checks"]["database"] == {"status": "down"}
