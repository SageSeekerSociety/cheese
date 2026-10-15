"""互联网技能直连这条路的接线：取回 → 按规范检验 → 落盘 → 重扫。

route 只是一层接线，所以这里把外面的三样全部换掉——机器（`device_hub.exec`）、
GitHub（`read_github`）、索引库（`MachineSkillService`）——只钉住接线本身：调用的顺序、
每种拒绝答什么码、以及 project_id 有没有真的搭在扫描命令上。
"""

import json
import uuid

import pytest

from app.api.routes import machine_skills
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.domain.agent.device_hub import DeviceCallError
from app.domain.device.supply import Supply
from app.domain.project_skill.importer import ReadSkill, normalize_skill_url

PROJECT = uuid.UUID("9fe447b6-6143-4467-8ab4-e9c663cd4505")
ADDRESS = "https://github.com/anthropics/skills/tree/main/skills/pdf"
SKILL_MD = "---\nname: pdf\ndescription: Read PDFs.\n---\n\nBody.\n"


def _reading(name: str = "pdf", description: str = "Read PDFs.") -> ReadSkill:
    return ReadSkill(
        name=name, title=name, description=description, body="Body.", raw=SKILL_MD
    )


class _Actor:
    authenticated = True
    user_id = 7
    handle = "alice"
    via = "token"


class _Resolver:
    def __init__(self):
        self.authorized: list[uuid.UUID] = []

    async def resolve(self) -> _Actor:
        return _Actor()

    async def authorize_project(self, actor, *, project_id):
        self.authorized.append(project_id)


class _Device:
    def __init__(self, owner: int = 7, supply: Supply = Supply.cloud):
        self.owner_user_id = owner
        self.supply = supply


class _Devices:
    def __init__(self, owner: int = 7, supply: Supply = Supply.cloud):
        self._owner = owner
        self._supply = supply

    async def get_hosted_device(self, device_id):
        return _Device(self._owner, self._supply)


class _Service:
    """索引库的替身：找到/找不到一行，落盘后返回一个空索引。"""

    def __init__(self, session, existing=None):
        self._existing = existing

    async def find(self, device_id, name):
        return self._existing

    async def record_scan(self, device_id, found):
        return []


class _Row:
    def __init__(self, source_root: str):
        self.source_root = source_root


class _Hub:
    """`device_hub` 的替身：记下每次 exec 的 argv / stdin，按脚本作答。"""

    def __init__(self, replies: list[dict]):
        self.calls: list[dict] = []
        self._replies = replies

    async def exec(self, device_id, argv, *, stdin=None, timeout=None):
        self.calls.append({"argv": list(argv), "stdin": stdin})
        return self._replies.pop(0)


def _landed() -> dict:
    return {"stdout": '{"path": "/x"}\n', "stderr": "", "exit": 0, "truncated": False}


def _scanned() -> dict:
    said = json.dumps({"skills": [], "problems": [], "roots": [], "workspaces": []})
    return {"stdout": said + "\n", "stderr": "", "exit": 0, "truncated": False}


def _wire(
    monkeypatch,
    *,
    replies,
    read,
    owner=7,
    supply=Supply.cloud,
    existing=None,
    multi=None,
):
    hub = _Hub(replies)

    def _devices(db):
        return _Devices(owner, supply)

    def _service(db):
        return _Service(db, existing=existing)

    monkeypatch.setattr(machine_skills, "device_hub", hub)
    monkeypatch.setattr(machine_skills, "sql_device_service", _devices)
    monkeypatch.setattr(machine_skills, "MachineSkillService", _service)

    async def _read(url, *, keep_name=False):
        return await read(url, keep_name=keep_name)

    async def _unused_many(url, *, keep_name=False):
        raise AssertionError("must not read many for a single skill")

    monkeypatch.setattr(machine_skills, "read_github", _read)
    monkeypatch.setattr(
        machine_skills, "read_github_many", multi or _unused_many, raising=False
    )
    return hub


class _Db:
    async def commit(self) -> None:
        pass


async def _call(
    url: str = ADDRESS,
    project: uuid.UUID | None = PROJECT,
    kind: str = "skill",
    confirm: bool = True,
):
    """The route as the dialog drives it: most tests are about landing, so they
    confirm by default; the look (``confirm=False``) has its own test below."""
    resolver = _Resolver()
    result = await machine_skills.fetch_machine_skill(
        device_id="dev1",
        body=machine_skills.WebSkillRequest(
            url=url, kind=kind, project_id=project, confirm=confirm
        ),
        db=_Db(),
        resolver=resolver,
    )
    return result, resolver


async def test_it_fetches_checks_lands_and_rescans(monkeypatch):
    seen: dict = {}

    async def read(url, *, keep_name=False):
        seen["url"] = url
        seen["keep_name"] = keep_name
        skill = _reading()
        skill.files = {"scripts/run.py": "print(1)\n"}
        return skill

    hub = _wire(monkeypatch, replies=[_landed(), _scanned()], read=read)
    result, resolver = await _call()

    assert seen["url"] == ADDRESS
    assert seen["keep_name"] is True  # 落盘要保留它自己报的名字，不 slug
    assert resolver.authorized == [PROJECT]
    # 第一次 exec 是落盘：整份技能（SKILL.md 原文 + 配套文件）都嵌在那段程序里。
    assert "SKILL.md" in hub.calls[0]["stdin"]
    assert "Read PDFs." in hub.calls[0]["stdin"]
    assert "scripts/run.py" in hub.calls[0]["stdin"]
    # 第二次 exec 是重扫，project_id 作为 argv[1] 搭上去，argv[2] 说明这台机器
    # 是不是用户自己的（cloud 空着 —— 它是在平台供的机器上跑）。
    assert hub.calls[1]["argv"] == ["python3", "-", str(PROJECT), ""]
    assert result["landed"] is True
    assert result["skills"] == [] and result["problems"] == []
    # 落盘的清单：一颗技能，和跟着进去的文件名 —— 确认框就是拿这个画的。
    assert result["plan"] == [
        {
            "name": "pdf",
            "description": "Read PDFs.",
            "files": ["SKILL.md", "scripts/run.py"],
        }
    ]


async def test_a_self_hosted_machine_is_told_to_skip_its_own_homes(monkeypatch):
    """用户自己 enroll 的机器：重扫命令多带一个 "1"，机器上 ~/.claude 那些个人
    目录一概不读 —— 那台机器是人的，不是平台的。"""

    async def read(url, *, keep_name=False):
        return _reading()

    hub = _wire(
        monkeypatch,
        replies=[_landed(), _scanned()],
        read=read,
        supply=Supply.self_hosted,
    )
    await _call()
    assert hub.calls[1]["argv"] == ["python3", "-", str(PROJECT), "1"]


async def test_a_skill_that_breaks_the_spec_is_refused_before_writing(monkeypatch):
    async def read(url, *, keep_name=False):
        return _reading(name="has space")

    hub = _wire(monkeypatch, replies=[_landed(), _scanned()], read=read)
    with pytest.raises(UnprocessableEntityError):
        await _call()
    assert hub.calls == []  # 一个字都没往那台机器上写


async def test_a_machine_that_is_not_yours_is_a_404(monkeypatch):
    async def read(url, *, keep_name=False):
        raise AssertionError("must not get as far as fetching")

    _wire(monkeypatch, replies=[], read=read, owner=999)
    with pytest.raises(NotFoundError):
        await _call()


async def test_a_machine_that_refuses_to_write_is_a_502(monkeypatch):
    async def read(url, *, keep_name=False):
        return _reading()

    refused = "cannot write ~/.cheese/web-skills (denied)"
    _wire(
        monkeypatch,
        replies=[{"stdout": "", "stderr": refused, "exit": 1, "truncated": False}],
        read=read,
    )
    with pytest.raises(DeviceCallError) as raised:
        await _call()
    assert "denied" in str(raised.value)


async def test_a_name_the_machine_already_has_is_refused(monkeypatch):
    async def read(url, *, keep_name=False):
        return _reading()

    hub = _wire(
        monkeypatch,
        replies=[_landed(), _scanned()],
        read=read,
        existing=_Row("/Users/me/.claude/skills/pdf"),
    )
    with pytest.raises(UnprocessableEntityError):
        await _call()
    assert hub.calls == []


async def test_a_name_that_already_came_from_the_web_may_land_again(monkeypatch):
    async def read(url, *, keep_name=False):
        return _reading()

    hub = _wire(
        monkeypatch,
        replies=[_landed(), _scanned()],
        read=read,
        existing=_Row("/Users/me/.cheese/web-skills/pdf"),
    )
    await _call()
    assert len(hub.calls) == 2


def test_a_github_address_is_taken_as_it_is():
    assert normalize_skill_url(ADDRESS) == ADDRESS


def test_a_raw_skill_md_link_becomes_the_folder_it_sits_in():
    assert (
        normalize_skill_url(
            "https://raw.githubusercontent.com/o/r/HEAD/skills/pdf/SKILL.md"
        )
        == "https://github.com/o/r/tree/HEAD/skills/pdf"
    )


def test_any_other_host_is_refused():
    for url in (
        "https://example.com/skills/pdf/SKILL.md",
        "https://gitlab.com/o/r/-/tree/main/skills/pdf",
        "ftp://github.com/o/r",
        "https://raw.githubusercontent.com/o/r/HEAD/README.md",
    ):
        with pytest.raises(UnprocessableEntityError):
            normalize_skill_url(url)


async def test_a_plugin_lands_every_skill_it_bundles(monkeypatch):
    async def multi(url, *, keep_name=False):
        assert keep_name is True
        return [_reading("alpha"), _reading("beta")]

    hub = _wire(
        monkeypatch,
        replies=[_landed(), _landed(), _scanned()],
        read=None,
        multi=multi,
    )
    result, _ = await _call(kind="plugin")

    assert len(hub.calls) == 3  # 两次落盘，一次重扫
    assert hub.calls[0]["argv"] == ["python3", "-"]
    assert "alpha" in hub.calls[0]["stdin"]
    assert "beta" in hub.calls[1]["stdin"]
    assert hub.calls[2]["argv"] == ["python3", "-", str(PROJECT), ""]
    assert result["landed"] is True
    assert [item["name"] for item in result["plan"]] == ["alpha", "beta"]


async def test_a_plugin_whose_second_skill_is_refused_writes_nothing(monkeypatch):
    async def multi(url, *, keep_name=False):
        return [_reading("alpha"), _reading("has space")]

    hub = _wire(monkeypatch, replies=[_landed(), _scanned()], read=None, multi=multi)
    with pytest.raises(UnprocessableEntityError):
        await _call(kind="plugin")
    assert hub.calls == []  # 全看完才动手：第一颗也没落


async def test_an_unconfirmed_fetch_is_a_look_and_writes_nothing(monkeypatch):
    """`confirm: false` 只答复「会写什么」—— 一个字节都不碰那台机器。"""

    async def read(url, *, keep_name=False):
        skill = _reading()
        skill.files = {"scripts/run.py": "print(1)\n"}
        return skill

    hub = _wire(monkeypatch, replies=[_landed(), _scanned()], read=read)
    result, _ = await _call(confirm=False)

    assert hub.calls == []  # 没落盘、也没重扫
    assert result == {
        "landed": False,
        "plan": [
            {
                "name": "pdf",
                "description": "Read PDFs.",
                "files": ["SKILL.md", "scripts/run.py"],
            }
        ],
    }


async def test_an_unconfirmed_look_still_refuses_what_the_landing_would(monkeypatch):
    """检验在「看一眼」时也做：名字被占、不符规范，当场说，不等人点了确认才翻脸。"""

    async def read(url, *, keep_name=False):
        return _reading()

    hub = _wire(
        monkeypatch,
        replies=[_landed(), _scanned()],
        read=read,
        existing=_Row("/Users/me/.claude/skills/pdf"),
    )
    with pytest.raises(UnprocessableEntityError):
        await _call(confirm=False)
    assert hub.calls == []


async def test_the_market_is_the_vendors_listing_and_nothing_of_the_callers(
    monkeypatch,
):
    seen = {}

    async def catalogue():
        seen["called"] = True
        return {"skills": [], "plugins": [], "plugin_total": 0, "mcp": [], "notes": []}

    monkeypatch.setattr(machine_skills.skill_market, "catalogue", catalogue)
    got = await machine_skills.read_skill_market(resolver=_Resolver())
    assert seen["called"] is True
    assert got["plugin_total"] == 0
