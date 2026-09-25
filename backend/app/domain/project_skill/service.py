"""Save, edit, confirm and ship a project's own skills.

What ships is always a confirmed revision: an AI teammate's draft or edit waits
for a person, and while it waits the last confirmed revision keeps shipping.
After every change the project's folder under ``.project-skills`` is rebuilt
from the confirmed revisions; each session launch reads that folder.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.skills import (
    RESERVED_SKILL_NAMES,
    SKILL_FILE_SUFFIXES,
    SKILL_HEREDOC_MARKER,
    native_skill_files,
)
from app.domain.project_skill.models import ProjectSkill, ProjectSkillRevision

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_FILE_BYTES = 200_000
MAX_FILES = 30
FIELDS = ("title", "description", "inputs", "steps", "outputs", "files")


def _now() -> datetime:
    return datetime.now(UTC)


def mirror_root(project_id: uuid.UUID) -> Path:
    return Path(settings.workspace_root) / ".project-skills" / str(project_id)


def _validate_files(files: dict) -> dict[str, str]:
    if not isinstance(files, dict):
        raise ValidationError("配套文件要是「路径 → 内容」")
    if len(files) > MAX_FILES:
        raise ValidationError(f"配套文件最多 {MAX_FILES} 个")
    out: dict[str, str] = {}
    for raw, content in files.items():
        path = PurePosixPath(str(raw))
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.name == "SKILL.md"
        ):
            raise ValidationError(f"配套文件路径不合法：{raw}")
        if path.suffix not in SKILL_FILE_SUFFIXES:
            allowed = "、".join(SKILL_FILE_SUFFIXES)
            raise ValidationError(f"{raw}：只能是文本文件（{allowed}）")
        if not isinstance(content, str):
            raise ValidationError(f"{raw}：内容要是文本")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise ValidationError(f"{raw} 超过 {MAX_FILE_BYTES // 1000} KB")
        if SKILL_HEREDOC_MARKER in content:
            raise ValidationError(f"{raw} 含有保留字 {SKILL_HEREDOC_MARKER}")
        out[path.as_posix()] = content
    return out


def _content(row: ProjectSkill) -> dict:
    return {key: getattr(row, key) for key in FIELDS}


def render_skill(name: str, content: dict, *, revision: int, confirmed_by: str) -> str:
    """The SKILL.md a session reads: a method, with this run's inputs left open."""
    files = sorted(content.get("files") or {})
    lines = [
        "---",
        f"name: {name}",
        "description: "
        + json.dumps(
            f"{content['description']}（项目工作方法「{content['title']}」）",
            ensure_ascii=False,
        ),
        "---",
        "",
        f"# {content['title']}",
        "",
        content["description"],
        "",
        f"项目成员保存的工作方法，第 {revision} 版，由 {confirmed_by} 确认。"
        "每次使用都以这一次用户给的输入为准，不沿用以前某一次的具体材料；"
        "缺少必需的输入就先问用户。",
        "",
        "## 需要的输入",
        "",
        content.get("inputs") or "（按用户这次给的材料）",
        "",
        "## 步骤与规则",
        "",
        content["steps"],
        "",
        "## 输出要求",
        "",
        content.get("outputs") or "（按用户这次的要求）",
    ]
    if files:
        lines += ["", "## 配套文件", ""]
        lines += [f"- `$CLAUDE_CONFIG_DIR/skills/{name}/{path}`" for path in files]
    return "\n".join(lines) + "\n"


class ProjectSkillService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, skill_id: uuid.UUID) -> ProjectSkill:
        row = await self._session.get(ProjectSkill, skill_id)
        if row is None:
            raise NotFoundError("没有这个工作方法")
        return row

    async def list(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        return list(
            await self._session.scalars(
                select(ProjectSkill)
                .where(ProjectSkill.project_id == project_id)
                .order_by(ProjectSkill.created_at)
            )
        )

    async def revisions(self, skill_id: uuid.UUID) -> list[ProjectSkillRevision]:
        return list(
            await self._session.scalars(
                select(ProjectSkillRevision)
                .where(ProjectSkillRevision.skill_id == skill_id)
                .order_by(ProjectSkillRevision.revision.desc())
            )
        )

    def _apply(self, row: ProjectSkill, changes: dict) -> None:
        for key in ("title", "description", "inputs", "steps", "outputs"):
            if changes.get(key) is not None:
                setattr(row, key, str(changes[key]).strip())
        if changes.get("files") is not None:
            row.files = _validate_files(changes["files"])
        if not row.title or not row.description or not row.steps:
            raise ValidationError("工作方法要有名称、用途和步骤")

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        by: str,
        by_agent: bool,
        name: str,
        fields: dict,
    ) -> ProjectSkill:
        name = (name or "").strip().lower()
        if not NAME.match(name):
            raise ValidationError(
                "名字只能用小写字母、数字和连字符，2–48 个字符，例如 weekly-report"
            )
        if name in RESERVED_SKILL_NAMES:
            raise ValidationError(f"「{name}」是平台内置技能的名字，换一个")
        taken = await self._session.scalar(
            select(ProjectSkill.id).where(
                ProjectSkill.project_id == project_id, ProjectSkill.name == name
            )
        )
        if taken is not None:
            raise ValidationError(f"这个项目里已经有叫「{name}」的工作方法")
        row = ProjectSkill(
            id=uuid.uuid4(),
            project_id=project_id,
            name=name,
            title="",
            description="",
            steps="",
            files={},
            state="draft",
            source_topic_id=topic_id,
            proposed_by=by,
        )
        self._apply(row, fields)
        self._session.add(row)
        await self._session.flush()
        if not by_agent:
            await self.confirm(row, by=by, note="创建")
        return row

    async def update(
        self, row: ProjectSkill, *, by: str, by_agent: bool, changes: dict
    ):
        self._apply(row, changes)
        if by_agent:
            row.state = "draft"
            row.proposed_by = by
            await self._session.flush()
        else:
            await self.confirm(row, by=by, note="修改")
        return row

    async def confirm(
        self, row: ProjectSkill, *, by: str, note: str = "确认"
    ) -> ProjectSkill:
        latest = await self._session.scalar(
            select(ProjectSkillRevision)
            .where(ProjectSkillRevision.skill_id == row.id)
            .order_by(ProjectSkillRevision.revision.desc())
            .limit(1)
        )
        content = _content(row)
        if latest is not None and latest.content == content:
            revision = latest.revision
        else:
            revision = (latest.revision if latest else 0) + 1
            self._session.add(
                ProjectSkillRevision(
                    id=uuid.uuid4(),
                    skill_id=row.id,
                    revision=revision,
                    content=content,
                    confirmed_by=by,
                    note=note,
                    created_at=_now(),
                )
            )
        row.state = "active"
        row.shipped_revision = revision
        row.confirmed_by = by
        row.confirmed_at = _now()
        await self._session.flush()
        return row

    async def restore(
        self, row: ProjectSkill, revision: int, *, by: str
    ) -> ProjectSkill:
        old = await self._session.scalar(
            select(ProjectSkillRevision).where(
                ProjectSkillRevision.skill_id == row.id,
                ProjectSkillRevision.revision == revision,
            )
        )
        if old is None:
            raise NotFoundError("没有这一版")
        for key in FIELDS:
            setattr(row, key, old.content[key])
        return await self.confirm(row, by=by, note=f"恢复到第 {revision} 版")

    async def delete(self, row: ProjectSkill) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def publish(self, project_id: uuid.UUID) -> None:
        """Rebuild the project's shipped folder from its confirmed revisions."""
        root = mirror_root(project_id)
        staging = root.with_name(f"{root.name}.{uuid.uuid4().hex}")
        staging.mkdir(parents=True)
        rows = await self._session.execute(
            select(ProjectSkill, ProjectSkillRevision)
            .join(
                ProjectSkillRevision,
                (ProjectSkillRevision.skill_id == ProjectSkill.id)
                & (ProjectSkillRevision.revision == ProjectSkill.shipped_revision),
            )
            .where(ProjectSkill.project_id == project_id)
        )
        for skill, revision in rows:
            folder = staging / skill.name
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                render_skill(
                    skill.name,
                    revision.content,
                    revision=revision.revision,
                    confirmed_by=revision.confirmed_by,
                ),
                encoding="utf-8",
            )
            for path, text in (revision.content.get("files") or {}).items():
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        retired = root.with_name(f"{root.name}.old.{uuid.uuid4().hex}")
        if root.exists():
            root.rename(retired)
        staging.rename(root)
        shutil.rmtree(retired, ignore_errors=True)


def project_skill_files(project_id: uuid.UUID | str | None) -> dict[str, str]:
    """{"skills/<name>/<path>": text} for the project's confirmed skills."""
    if not project_id:
        return {}
    root = mirror_root(uuid.UUID(str(project_id)))
    if not root.is_dir():
        return {}
    files: dict[str, str] = {}
    for source in sorted(root.rglob("*")):
        if source.is_file() and not source.is_symlink():
            relative = source.relative_to(root).as_posix()
            files[f"skills/{relative}"] = source.read_text(encoding="utf-8")
    return files


def project_skill_names(project_id: uuid.UUID | str | None) -> list[str]:
    return sorted({path.split("/")[1] for path in project_skill_files(project_id)})


def session_skill_files(project_id: uuid.UUID | str | None) -> dict[str, str]:
    """Everything a session in this project gets: the platform's, then its own."""
    return {**native_skill_files(), **project_skill_files(project_id)}
