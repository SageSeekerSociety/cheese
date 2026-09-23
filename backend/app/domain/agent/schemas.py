"""网关模型管理页的请求体。

这个页面上有三件事会被提交：新建或编辑一条模型、停用或启用它、改一个项目的刹车值。
每件事一个模型，字段名与 `frontend/src/api.ts` 那份类型接口一一对应 —— 后端是这段契约
的权威，前端照着它写（同 `admin/schemas.py` 的 `AdminAdd`）。

这里只管**形状**：字段名、类型、名字的字符集与长度、上游地址必须是 https。业务上的判断
一个都不放：`selectable=true` 却缺输入或输出单价要由服务层拒（那句「无价模型会让项目的
max_budget 刹车静默失效」得和网关目录刷新、审计落库一起说，放进 pydantic 只会让同一句话
有第二份、让路由层也当一次裁判），而「config 来源不许写」「这条模型不存在」都要读网关或
库才答得上来，同样在服务层。路由与服务合起来才是契约 §2.3 那一张失败语义表。
"""

from pydantic import BaseModel, Field, field_validator

# 模型名允许的字符集。它同时是网关路由里的 key 和页面上唯一的标识，放空格或斜杠进来
# 只会让它在 URL 与日志里被编成一团，没有好处。长度跟着契约的 1..64。
_NAME_PATTERN = r"^[A-Za-z0-9._-]+$"


class ModelPrices(BaseModel):
    """一条模型的四个单价。

    用 `None` 表示「没定」，不是 0：0 是「定成免费」，`None` 是「这一栏空着」，网关的写
    接口也按这个区分（`GatewayAdmin._price_params` 只传真数字，`None` 一个都不发）。
    cache_read / cache_creation 允许缺，是因为不少上游根本没给缓存计价。
    """

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_creation: float | None = None


class ModelCapabilities(BaseModel):
    """能勾的能力。页面认这三项；网关那套 `supports_*` 字段名不出这个包。

    `adaptive_thinking` 必须**声明在这里**，不能指望 pydantic 把它顺手留下：网关侧
    的 `supports_adaptive_thinking` 读写都认它（`gateway_admin` 的读路径会把它读回来
    显示），但一个没声明的键会被 pydantic 默认丢弃 —— 前端勾上、保存后请求体里根本
    没有它，写网关时什么都不会发生，而读回来却显示得出来。那是一个静默的空操作。
    """

    reasoning: bool = False
    vision: bool = False
    adaptive_thinking: bool = False


class _ModelBodyBase(BaseModel):
    """新建与编辑共用的那部分形状。

    两处真正共用的一条规则：上游地址必须 https。明文 http 会让上游凭据在路上裸奔，这里
    直接不给人填错的机会；留空表示「用上游默认端点」，所以空串一并按「没填」处理，免得
    前端把「没填」写成空串时被当成一个非法地址拒掉。
    """

    api_base: str | None = None
    api_key: str | None = None
    api_key_unchanged: bool = False
    label: str | None = None
    prices: ModelPrices | None = None
    capabilities: ModelCapabilities | None = None
    # 随每次调用透传给上游的额外头（订阅导入程序化写入；v1 前端表单不暴露它，
    # PATCH 缺省即保留 —— 编辑订阅模型的标签/价格不会弄丢头）。键限 64、值限
    # 200：头会原样进 HTTP 请求行，不挡住注释/换行注入就是给别人开门。
    extra_headers: dict[str, str] | None = None

    @field_validator("extra_headers")
    @classmethod
    def _headers_bounded(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        for key, item in value.items():
            if not key or len(key) > 64:
                raise ValueError("extra_headers 的键必须是 1..64 个字符")
            if any(ch in key for ch in "\r\n:"):
                raise ValueError("extra_headers 的键不能包含冒号或换行")
            if len(item) > 200 or "\r" in item or "\n" in item:
                raise ValueError("extra_headers 的值不能超过 200 个字符且不能换行")
        return value

    @field_validator("api_base")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not value.startswith("https://"):
            raise ValueError("api_base 必须是 https:// 开头的地址")
        return value


class ModelCreate(_ModelBodyBase):
    """`POST /admin/gateway/models` 的体 —— 新建一条运行时模型。

    名字按 `_NAME_PATTERN` 收；`api_key_unchanged` 在新建时没有意义（还没有凭据可保），
    留着是为了让新建与编辑共用同一个体，前端不必为两处写两份形状。
    """

    name: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    upstream_model: str = Field(min_length=1, max_length=200)
    selectable: bool = False


class ModelUpdate(_ModelBodyBase):
    """`PATCH /admin/gateway/models/{name}` 的体 —— 只传要改的，缺的都不动。

    每个字段都可有可无，`None` 一律表示「这次别碰它」。两处例外，都在签名上：

    - `api_key_unchanged` 默认 `true`：编辑界面不回显上游凭据，所以一次没带 key 的提交
      绝不能把现有 key 清空 —— 那会让这条模型下一次调用直接失败。
    - `name` 虽然在体里（前端把同一个表单原样提交），但**改名不支持**：真正的名字以
      路径上的 `{name}` 为准，服务层的 `update` 也是分开收这两个的。
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_NAME_PATTERN
    )
    upstream_model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key_unchanged: bool = True
    selectable: bool | None = None


class BudgetUpdate(BaseModel):
    """`PUT /admin/gateway/projects/{project_id}/budget` 的体。

    `None` 是「清掉这道刹车」，与「填一个 0」是两件事：0 会让项目一步也走不动，清空才是
    回到不限。所以这一栏必须显式给（缺字段直接 400），不能让「忘了传」被悄悄读成清空。
    """

    max_budget_usd: float | None


class BlockedUpdate(BaseModel):
    """`POST /admin/gateway/models/{name}/blocked` 的体。"""

    blocked: bool
