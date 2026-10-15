"""本包够不着的那些事，写在这里由调用方递进来。

没有一件是本包做得成的：`Announce` 和 `DefaultTeammate` 落在第 5 层的 `agent`、
`agent_instance` 里，`ComputeCredits` 落在第 3 层的 `usage` 里，`FileStore` 和
`LibraryFiles` 落在第 4 层的 `library` 里（`docs/manual/dev/architecture.md` 的层表）。
本包只写下自己要的那几次调用，谁在门口谁把它递进来 —— 和 `webhook/ports.py` 的
`Poster`、`integration/ports.py` 的 `RoomFiles` 同一条规矩：口子按消费者分开写，实现
按提供者放（`app/domain/agent_instance/adapters.py`、`app/domain/usage/adapters.py`）。

返回值是不透明的：``announce()`` 交回它写下的那个 ``Block``，而 ``block`` 也在本包
上面。本包这里不关心落下了哪一行，只声明**可能什么都没落**（房间没了）。
"""

import uuid
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project


class Announce(Protocol):
    """把一句话落进一个房间、按 `meta` 投出去。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        task_id: uuid.UUID | None,
        content: str,
        meta: dict | None,
    ) -> object | None:
        """Returns what it landed, or ``None`` if the place is gone."""
        ...


class DefaultTeammate(Protocol):
    """这个项目的默认 AI 队友：还没有就播一个，人设按调用方说的落上去。

    项目的芝士和它在总览里的席位，与项目同一个事务里出生（结论 4、不变量 I9b）：
    一个项目不会有「还没有 agent」的那一刻。播种是全仓唯一那一处，在
    `AgentInstanceService.materialize_default` 里，本包够不着。

    一次调用答三件事：把实例播出来、给它一个名字（``display_name``）、给它一份人设
    （``type_name``）。传的是**那一行 Project 本身**而不是它的 id：播种会就地写回
    ``default_agent_instance_id``，也要读它上面的 ``root_topic_id`` 才坐得下席位。
    人设只在它**还没有**的时候落 —— 一个项目自己挑过的人设不该被后来的一次赛题报名
    覆盖；建项目那一次实例刚出生，本来也没有人设，所以那一条路不受这条规矩影响。

    形状就是 `AgentInstanceService.materialize_default` 加它的 `set_type`，改完就地
    flush，调用方不用自己刷。
    """

    async def __call__(
        self,
        session: AsyncSession,
        project: Project,
        *,
        display_name: str | None = None,
        type_name: str | None = None,
    ) -> None: ...


class WarmPreview(Protocol):
    """一份文件刚存下来，就顺手把它转成浏览器能看的那种。

    房间里保存的 Office 文件要转成 PDF 才预览得动，而「怎么转」在第 5 层的
    `preview` 里（`app/domain/preview/office.py`），本包够不着。文件刚写下来的那
    一刻只有本包知道，所以由本包**在存完之后**叫一声，转出来的东西落在别人家的
    缓存里。

    形状就是 ``preview.office.prewarm``：收下刚存的字节和它的路径，立刻返回。它
    是发出去不管的（一次保存不该等 LibreOffice），而且没配渲染服务时自己什么都不
    做 —— 所以调用方把那个函数原样递进来即可，不需要适配。
    """

    def __call__(self, data: bytes, path: str) -> None: ...


class ComputeCredits(Protocol):
    """某个项目领没领过一道赛题的资源包，没领就领一份。

    审过一道赛题报名，项目要继承机构协议里的算力：这是一笔「按项目限定用途」的
    额度（``GrantSource.TASK_EARMARK``），账本在 `app.domain.usage.ledger.Ledger`
    里，比本包高一层，本包够不着。

    形状只答本包真正要问的那两件事，不是账本的形状：先问**领过没有**
    （``has_earmark``），没领过再领（``grant_earmark``）。`Ledger.earmarks` 本来
    交回的是一串 ``ComputeGrant``（别人家的类），本包认不出也不该认得，所以这里
    折成一个布尔。账本的 ``grant_earmark`` 返回新建的那一行，本包用不上，折成
    ``None``。

    ``source_task_id`` 是 ``int | None``：额度上记的是哪道赛题发的，赛题之外发的
    那种（``None``）与本包无关，这里只是照账本的形状声明得了。
    """

    async def has_earmark(
        self, project_id: uuid.UUID, source_task_id: int | None
    ) -> bool:
        """这个项目已经领过这道赛题的资源包没有。"""
        ...

    async def grant_earmark(
        self,
        *,
        project_id: uuid.UUID,
        source_task_id: int | None,
        credits_total: float,
    ) -> None:
        """给这个项目发一份只能在这里花的算力。"""
        ...


class FileStore(Protocol):
    """本包读写的那几份字节在盘上哪儿。

    房间里的文件、它每一版的字节、还有交出去的那一版留存下来的快照，认的都是项目
    /房间/路径/卡这几样地址，落地在第 4 层的 `app/domain/library/service.py` 里，
    本包够不着。

    这里写的每一次调用，那个模块**原样就答得了** —— 地址由本包给出，进出的都是
    内置类型（字节、路径、有没有），所以调用方把模块本身递进来即可，不需要适配：
    和 `integration/ports.py` 的 `RoomFiles` 一样（`app/api/routes/integrations.py`
    递的就是那个模块）。

    ``MAX_FILE_BYTES`` 是房间里一份文件落地时的上限。本包要它是因为一版历史只留得下
    上限以内的东西（``room_files.save_room_file``）；数本身跟着落地的那一层，这里
    只是照读。
    """

    MAX_FILE_BYTES: int

    def read_room_file(
        self, project_id: uuid.UUID, room_id: uuid.UUID, path: str
    ) -> bytes: ...

    def write_room_file(
        self, project_id: uuid.UUID, room_id: uuid.UUID, path: str, data: bytes
    ) -> None: ...

    def room_file_exists(
        self, project_id: uuid.UUID, room_id: uuid.UUID, path: str
    ) -> bool: ...

    def write_revision_blob(
        self, project_id: uuid.UUID, room_id: uuid.UUID, data: bytes
    ) -> str:
        """留下这一份字节，交回它的内容哈希。"""
        ...

    def read_revision_blob(
        self, project_id: uuid.UUID, room_id: uuid.UUID, digest: str
    ) -> bytes: ...

    def artifact_snapshot_path(
        self, project_id: uuid.UUID, card_id: uuid.UUID, name: str
    ) -> Path:
        """一版交付物留存下来的那一份在盘上的位置。"""
        ...

    def artifact_snapshot_size(
        self, project_id: uuid.UUID, card_id: uuid.UUID, name: str
    ) -> int | None:
        """那一份有多大；从来没有留存过字节时给 ``None``。"""
        ...


class LibraryFiles(Protocol):
    """资料库里那些资料：留一份进去，按名字把字节找出来。

    房间里按「保存到资料库」留下的那一份进的是资料库，项目导出要把这个项目库里
    每一份字节搬进压缩包 —— 两件事认的都是资料库那几行，在
    `app/domain/library/records.py` 里，本包够不着。这里写的每一次调用，那个模块
    原样就答得了。

    ``stored`` 交回的是一串 ``(名字, 存储, 键)``，不是 ``LibraryFileRecord``：搬字节
    发生在事务结束之后、别人的域里（导出），本包只拿这三样，键读回字节再叫
    ``read_blob``。
    """

    async def add(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        filename: str,
        data: bytes,
        added_by: str | None,
        room_id: uuid.UUID | None,
    ) -> str:
        """放进一份新资料，交回它在那里的名字（撞名加 ``(n)``，不覆盖）。"""
        ...

    async def stored(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> list[tuple[str, str, str]]:
        """这个项目库里每一份的名字、存储和键。"""
        ...

    def read_blob(self, location: str, key: str) -> bytes:
        """那双 ``(存储, 键)`` 底下的字节。"""
        ...
