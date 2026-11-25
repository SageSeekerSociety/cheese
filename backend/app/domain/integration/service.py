"""Connect, lend and use a person's mailbox or Feishu account.

Who may do what: only the owner manages a connection, names the projects whose
AI teammates may use it, and sends a drafted mail. A teammate in a granted
project may search, read, fetch attachments, write drafts, and read and write
Feishu documents — always as the owner's account, never as the platform's.

The Feishu app those documents are reached through belongs to a team
(``TeamFeishuApp``): a self-built app lives in one Feishu tenant, so a company's
team sets its company's app, and a person on their own sets one on their
personal team. A member's connection names the team and keeps only their own
tokens; the app's credentials are read from the team at every call. The
platform holds no app of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.integration import mail
from app.domain.integration.feishu import FeishuClient, FeishuSettings
from app.domain.integration.mail import IntegrationError, MailSettings
from app.domain.integration.models import Integration, MailDraft, TeamFeishuApp
from app.domain.integration.ports import RoomFiles, TeammateNames
from app.domain.team.services import team_service

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def seal(row: Integration, secret: dict) -> None:
    row.secret = encrypt(
        Purpose.INTEGRATION_SECRET, json.dumps(secret), bound_to=str(row.id)
    )


def unseal(row: Integration) -> dict:
    return json.loads(
        decrypt(Purpose.INTEGRATION_SECRET, row.secret, bound_to=str(row.id))
    )


def mail_settings(row: Integration) -> MailSettings:
    config = row.config
    return MailSettings(
        imap_host=config["imap_host"],
        imap_port=int(config["imap_port"]),
        smtp_host=config["smtp_host"],
        smtp_port=int(config["smtp_port"]),
        username=config["username"],
        password=unseal(row)["password"],
        security=config.get("security", "ssl"),
    )


#: Failures only the connection's owner can mend — authorize again, or connect
#: through an app Feishu accepts. The row is marked failed and waits for them.
NEEDS_OWNER = frozenset({"auth_failed", "credentials_invalid"})


def feishu_redirect_uri() -> str:
    """Where Feishu sends a browser back after authorizing: the callback route.
    A team's app lists it as its redirect URL."""
    return f"{settings.frontend_url}/api/integrations/feishu/callback"


def _team_app_binding(team_id: int) -> str:
    return f"team-feishu-app:{team_id}"


def seal_team_app(row: TeamFeishuApp, app_secret: str) -> None:
    row.secret = encrypt(
        Purpose.INTEGRATION_SECRET,
        json.dumps({"app_secret": app_secret}),
        bound_to=_team_app_binding(row.team_id),
    )


def team_app_secret(row: TeamFeishuApp) -> str:
    return json.loads(
        decrypt(
            Purpose.INTEGRATION_SECRET,
            row.secret,
            bound_to=_team_app_binding(row.team_id),
        )
    ).get("app_secret", "")


def feishu_settings(
    row: Integration, app: TeamFeishuApp | None, *, user_only: bool = False
) -> FeishuSettings:
    """What this connection calls Feishu with: the team app's credentials (or,
    on an older row that carries its own, those), and the owner's own tokens.

    The owner's tokens were issued under one App ID (``authorized_app_id``, kept
    with them). A team that has since switched to another app has nothing those
    tokens can be used with: they are left out, never paired with the new app's
    credentials, and the owner authorizes again under it.
    """
    secret = unseal(row)
    if app is not None:
        app_id, app_secret, domain = app.app_id, team_app_secret(app), app.domain
    else:
        app_id = row.config["app_id"]
        app_secret = secret.get("app_secret") or ""
        domain = row.config.get("domain", "feishu")
    issued_under = row.config.get("authorized_app_id")
    tokens = issued_under is None or issued_under == app_id
    return FeishuSettings(
        app_id=app_id,
        app_secret=app_secret,
        domain=domain,
        user_access_token=secret.get("user_access_token") if tokens else None,
        user_token_expires_at=secret.get("user_token_expires_at") if tokens else None,
        refresh_token=secret.get("refresh_token") if tokens else None,
        refresh_token_expires_at=(
            secret.get("refresh_token_expires_at") if tokens else None
        ),
        folders=list(row.config.get("folders") or []),
        user_only=user_only or not tokens,
    )


def feishu_kind(row: Integration) -> str:
    """Which shape a Feishu row has: ``team`` (through a team's app, what every
    connection made now is), ``own_app`` (an older row carrying its own app
    credentials — it keeps working, and its owner is asked to reconnect through
    a team), or ``platform`` (made under the platform-wide app that no longer
    exists — there is nothing to call with until its owner reconnects)."""
    if row.config.get("team_id"):
        return "team"
    if row.config.get("app_id"):
        return "own_app"
    return "platform"


async def feishu_settings_for(
    session: AsyncSession, row: Integration
) -> FeishuSettings:
    """``feishu_settings`` with the team's app read now, so a rotated secret
    reaches every member at their next call.

    A connection whose team no longer has an app, or whose owner has left the
    team, has nothing to call with: it is an authorization to give again, and
    the owner is told so. On a shared team the app's own identity is never used
    for a member — only their own authorization is — so nobody reads what is
    shared with the team's app just by holding a connection; on their personal
    team it is their own app. An older row is read as it was written.
    """
    kind = feishu_kind(row)
    if kind == "own_app":
        return feishu_settings(row, None)
    if kind == "platform":
        raise IntegrationError("credentials_invalid", say("feishuNeedsTeamApp"))
    team_id = int(row.config["team_id"])
    teams = team_service(session)
    team = await teams.get_team(team_id)
    app = await session.get(TeamFeishuApp, team_id) if team is not None else None
    if team is None or app is None:
        raise IntegrationError(
            "auth_failed", say("feishuTeamAppRemoved", team=row.label)
        )
    if not await teams.is_team_member(team_id, row.owner_user_id):
        raise IntegrationError("auth_failed", say("feishuTeamLeft", team=row.label))
    settings_ = feishu_settings(
        row, app, user_only=team.personal_owner_user_id != row.owner_user_id
    )
    issued_under = row.config.get("authorized_app_id")
    if issued_under is not None and issued_under != app.app_id:
        raise IntegrationError(
            "auth_failed", say("feishuTeamAppChanged", team=row.label)
        )
    return settings_


def feishu_connection_view(row: Integration | None) -> dict | None:
    """A person's own connection through one team's app, as far as the team's
    page and the connections overview need it: which row, and whether they have
    authorized it. ``None``: not connected."""
    if row is None:
        return None
    return {
        "id": str(row.id),
        "status": row.status,
        "user_authorized": bool(unseal(row).get("refresh_token")),
    }


def team_app_view(row: TeamFeishuApp | None) -> dict:
    """A team's Feishu app as its settings page sees it. Never the secret."""
    return {
        "configured": row is not None,
        "app_id": row.app_id if row else "",
        "domain": row.domain if row else "feishu",
        "updated_by": row.updated_by if row else "",
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "redirect_uri": feishu_redirect_uri(),
    }


class TeamFeishuAppService:
    """A team's Feishu app: set, changed or removed by whoever may change the
    team's settings (the route checks that), read by its members."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, team_id: int) -> TeamFeishuApp | None:
        return await self._session.get(TeamFeishuApp, team_id)

    async def require_member(self, team_id: int, user_id: int) -> None:
        """Only someone on the team reads or changes its Feishu app — a platform
        admin overseeing the team without a seat in it included: the app is the
        credential the team's members connect their own accounts through, not a
        setting to manage from outside. A personal team's one member is its
        owner. Anyone else is answered as if there were no such team."""
        if not await team_service(self._session).is_team_member(team_id, user_id):
            raise NotFoundError(say("teamNotFoundById", team_id=team_id))

    async def save(
        self, *, team_id: int, app_id: str, app_secret: str, domain: str, by: str
    ) -> TeamFeishuApp:
        """Save it after proving it: one tenant token is asked for with the
        credentials, so a wrong App Secret is said here and nothing is kept. An
        empty ``app_secret`` keeps the one already stored — the page never shows
        it, so it cannot be retyped.

        Two saves at once are taken one after the other: the team's row is
        reserved (``ON CONFLICT DO NOTHING``, so a first save racing another
        meets it instead of failing) and locked before anything is read from
        it, so a save that keeps the stored secret reads the one the save before
        it wrote, never an older one it would then write back.

        Switching to another App ID leaves every member's authorization behind
        (their tokens were issued under the old app): those connections are
        cleared and marked to authorize again. Rotating the secret of the same
        App ID leaves them as they are.
        """
        if domain not in ("feishu", "lark"):
            raise ValidationError(say("feishuDomainInvalid"))
        now = _now()
        await self._session.execute(
            pg_insert(TeamFeishuApp)
            .values(
                team_id=team_id,
                app_id=app_id,
                domain=domain,
                secret="",
                updated_by=by,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[TeamFeishuApp.team_id])
        )
        row = await self._session.scalar(
            select(TeamFeishuApp)
            .where(TeamFeishuApp.team_id == team_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        reserved = row.secret == ""
        secret = app_secret or ("" if reserved else team_app_secret(row))
        if not secret:
            raise ValidationError(say("feishuAppSecretRequired"))
        await FeishuClient(
            FeishuSettings(app_id=app_id, app_secret=secret, domain=domain)
        ).tenant_token()
        switched = not reserved and row.app_id != app_id
        row.app_id, row.domain, row.updated_by, row.updated_at = (
            app_id,
            domain,
            by,
            now,
        )
        seal_team_app(row, secret)
        if switched:
            await self._send_members_back(team_id, "feishuTeamAppChanged")
        await self._session.flush()
        return row

    async def remove(self, team_id: int) -> None:
        row = await self._session.scalar(
            select(TeamFeishuApp)
            .where(TeamFeishuApp.team_id == team_id)
            .with_for_update()
        )
        if row is not None:
            await self._session.delete(row)
            await self._send_members_back(team_id, "feishuTeamAppRemoved")
            await self._session.flush()

    async def _send_members_back(self, team_id: int, key: str) -> None:
        """Every member's connection through this team's app loses the tokens it
        holds and is marked to authorize again, saying why."""
        rows = await self._session.scalars(
            select(Integration).where(
                Integration.provider == "feishu",
                Integration.config["team_id"].as_integer() == team_id,
            )
        )
        for member in rows:
            seal(member, {})
            member.config = {
                k: v for k, v in member.config.items() if k != "authorized_app_id"
            }
            member.status = "auth_failed"
            member.last_error = str(say(key, team=member.label))

    async def connection_for(self, user_id: int, team_id: int) -> Integration | None:
        """The person's own connection through this team's app, if they made one."""
        return await self._session.scalar(
            select(Integration).where(
                Integration.owner_user_id == user_id,
                Integration.provider == "feishu",
                Integration.config["team_id"].as_integer() == team_id,
            )
        )

    async def choices_for(self, user_id: int) -> list[dict]:
        """Every team the person is on, their personal team first, each with its
        app if it has one, whether this person may set it, and their own
        connection through it — the overview on 「我的连接」, each team linking
        to its own 「飞书应用」 section, where connecting happens."""
        teams = team_service(self._session)
        personal = await teams.ensure_personal_team(user_id)
        mine = list(await teams.get_teams_of_user(user_id))
        if all(t.id != personal.id for t in mine):
            mine.append(personal)
        mine.sort(key=lambda t: (t.personal_owner_user_id != user_id, t.id))
        apps = {
            a.team_id: a
            for a in (
                await self._session.scalars(
                    select(TeamFeishuApp).where(
                        TeamFeishuApp.team_id.in_([t.id for t in mine])
                    )
                )
            )
        }
        out = []
        for team in mine:
            app = apps.get(team.id)
            out.append(
                {
                    "team_id": team.id,
                    "team_name": team.name,
                    "team_handle": team.handle or "",
                    "personal": team.personal_owner_user_id == user_id,
                    "can_manage": team.personal_owner_user_id == user_id
                    or await teams.is_team_at_least_admin(team.id, user_id),
                    "app": {"app_id": app.app_id, "domain": app.domain}
                    if app
                    else None,
                    "connection": feishu_connection_view(
                        await self.connection_for(user_id, team.id)
                    ),
                }
            )
        return out


def keep_feishu_tokens(row: Integration, settings: FeishuSettings) -> None:
    """A refreshed user token is written back, or the next call refreshes again.
    The App ID it was issued under goes with it (``authorized_app_id``), so the
    token is never later paired with another app's credentials."""
    secret = unseal(row)
    fresh = {
        **secret,
        "user_access_token": settings.user_access_token,
        "user_token_expires_at": settings.user_token_expires_at,
        "refresh_token": settings.refresh_token,
        "refresh_token_expires_at": settings.refresh_token_expires_at,
    }
    if fresh != secret:
        seal(row, fresh)
    if settings.user_access_token and row.config.get("team_id"):
        if row.config.get("authorized_app_id") != settings.app_id:
            row.config = {**row.config, "authorized_app_id": settings.app_id}


#: How long before the refresh token stops working an authorization is renewed.
#: Feishu's lasts about a week, so an hourly look renews it with days to spare.
RENEW_BEFORE_S = 2 * 24 * 3600


def _access_lapses(settings: FeishuSettings) -> bool:
    """The user token will not last through a call."""
    return (
        bool(settings.user_access_token)
        and (settings.user_token_expires_at or 0) <= time.time() + 60
    )


def _refresh_lapses(settings: FeishuSettings) -> bool:
    """The refresh token is near its end; one whose end is not known (kept
    before its expiry was) is renewed so that it is."""
    if not settings.refresh_token:
        return False
    ends = settings.refresh_token_expires_at
    return ends is None or ends <= time.time() + RENEW_BEFORE_S


async def _settings_marking(session: AsyncSession, row: Integration) -> FeishuSettings:
    """``feishu_settings_for``, marking the row failed when there is nothing to
    call with any more, so the person's page says it."""
    try:
        return await feishu_settings_for(session, row)
    except IntegrationError as exc:
        row.status, row.last_error = "auth_failed", str(exc.args[0])
        await session.flush()
        raise


async def _renewed(
    session: AsyncSession,
    row: Integration,
    lapses: Callable[[FeishuSettings], bool],
) -> FeishuSettings:
    """The row's settings, its user token refreshed first when ``lapses`` says so.

    A refresh token is good for one refresh: two calls refreshing with the same
    one leave the second refused and the person told to authorize again. So the
    row is locked and read again before refreshing, and the new tokens are
    written back at once — a caller that waited on the lock finds them there and
    does not refresh a second time.
    """
    settings = await _settings_marking(session, row)
    if not lapses(settings):
        return settings
    if inspect(row).persistent:
        await session.refresh(row, with_for_update=True)
        settings = await _settings_marking(session, row)
        if not lapses(settings):
            return settings
    try:
        await FeishuClient(settings).refresh_user_token()
    except IntegrationError as exc:
        if exc.kind in NEEDS_OWNER:
            row.status, row.last_error = "auth_failed", str(exc.args[0])
            await session.flush()
        raise
    keep_feishu_tokens(row, settings)
    await session.flush()
    return settings


async def renew_feishu(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Refresh every Feishu authorization whose refresh token is near its end,
    so one the person has not used for a week still works. One that cannot be
    refreshed (Feishu ends every authorization a year after it was given) is
    marked failed, which the person's page says."""
    async with sessions() as session:
        ids = list(
            await session.scalars(
                select(Integration.id).where(Integration.provider == "feishu")
            )
        )
    for integration_id in ids:
        async with sessions() as session:
            row = await session.get(Integration, integration_id)
            # One already failed waits for the person to authorize again; one
            # made under the platform-wide app has nothing to renew with.
            if (
                row is None
                or row.status == "auth_failed"
                or feishu_kind(row) == "platform"
            ):
                continue
            try:
                await _renewed(session, row, _refresh_lapses)
            except IntegrationError as exc:
                # One Feishu did not answer for is tried again the next hour.
                if exc.kind not in NEEDS_OWNER:
                    continue
                # A failed one is marked so and kept; the scan goes on.
                row.status, row.last_error = "auth_failed", str(exc.args[0])
            await session.commit()


def public(row: Integration) -> dict:
    config = {k: v for k, v in row.config.items()}
    authorized = False
    status, last_error = row.status, row.last_error
    reconnect = None
    if row.provider == "feishu":
        authorized = bool(unseal(row).get("refresh_token"))
        kind = feishu_kind(row)
        # An older row is asked to reconnect through a team's app: one carrying
        # its own app keeps working meanwhile; one made under the platform-wide
        # app has nothing to call with, so it does not read as working.
        if kind != "team":
            reconnect = "team_app"
        if kind == "platform":
            status, last_error = "auth_failed", str(say("feishuNeedsTeamApp"))
    return {
        "id": str(row.id),
        "provider": row.provider,
        "label": row.label,
        "owner_handle": row.owner_handle,
        "config": config,
        "grants": row.grants,
        "status": status,
        "last_error": last_error,
        "last_checked_at": row.last_checked_at.isoformat()
        if row.last_checked_at
        else None,
        "user_authorized": authorized,
        #: ``team_app``: an older Feishu row whose owner should connect again
        #: through a team's app.
        "reconnect": reconnect,
    }


def draft_view(row: MailDraft) -> dict:
    return {
        "id": str(row.id),
        "integration_id": str(row.integration_id),
        "project_id": str(row.project_id),
        "topic_id": str(row.topic_id) if row.topic_id else None,
        "created_by": row.created_by,
        "to": row.to,
        "cc": row.cc,
        "subject": row.subject,
        "body": row.body,
        "attachments": row.attachments,
        "in_reply_to": row.in_reply_to,
        "status": row.status,
        "error": row.error,
        "confirmed_by": row.confirmed_by,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


#: RFC 2544's benchmarking range. Nothing public lives there, which is why
#: fake-ip proxies (Clash, mihomo, sing-box) hand it out as placeholders: every
#: outside name resolves into it and the proxy connects by name. Seen on dev,
#: 2026-09-27: imap.qq.com → 198.18.0.198. Such an address says nothing about
#: where the name really points, so the real answer is asked for instead.
FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _real_addresses(host: str) -> list[IPAddress]:
    """The host's addresses from public DNS over HTTPS, past the local proxy."""

    found: list[IPAddress] = []
    for kind in ("A", "AAAA"):
        try:
            answer = httpx.get(
                settings.integration_doh_url,
                params={"name": host, "type": kind},
                timeout=8,
            ).json()
        except Exception:  # any failure means "not verified"
            continue
        for record in answer.get("Answer") or []:
            if record.get("type") in (1, 28):
                try:
                    found.append(ipaddress.ip_address(record["data"]))
                except (KeyError, ValueError):
                    continue
    return found


def refuse_internal_host(host: str, what: str) -> None:
    """Refuse a host a person named that is this platform's own network.

    `what` names the host in the refusal (「邮件服务器」, 「MCP 服务器」). Blocking:
    it resolves the name, and asks public DNS past a fake-ip proxy's placeholder.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValidationError(say("hostNotFound", what=what, host=host)) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address in FAKE_IP_RANGE:
            real = _real_addresses(host)
            if not real:
                raise ValidationError(say("hostUnresolvable", what=what, host=host))
            for actual in real:
                _refuse_if_internal(host, actual, what)
            continue
        _refuse_if_internal(host, address, what)


def _refuse_if_internal(host: str, address: IPAddress, what: str) -> None:
    if not address.is_global or address.is_multicast:
        raise ValidationError(say("hostInternal", what=what, host=host))


def guard_mail_hosts(config: dict) -> None:
    """Refuse a mail server that is this platform's own network, or plaintext."""
    if settings.integration_allow_private_hosts:
        return
    if config.get("security") == "plain":
        raise ValidationError(say("mailServerNeedsEncryption"))
    for key in ("imap_host", "smtp_host"):
        refuse_internal_host(str(config.get(key) or ""), say("nounMailServer"))


class IntegrationService:
    def __init__(
        self, session: AsyncSession, room_files: RoomFiles, teammates: TeammateNames
    ):
        self._session = session
        self._room_files = room_files
        self._teammates = teammates

    async def owned(self, owner_user_id: int) -> list[Integration]:
        return list(
            await self._session.scalars(
                select(Integration)
                .where(Integration.owner_user_id == owner_user_id)
                .order_by(Integration.created_at)
            )
        )

    async def get_owned(
        self, integration_id: uuid.UUID, owner_user_id: int
    ) -> Integration:
        row = await self._session.get(Integration, integration_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise NotFoundError(say("integrationNotFound"))
        return row

    async def granted(self, project_id: uuid.UUID) -> list[Integration]:
        rows = await self._session.scalars(select(Integration))
        return [r for r in rows if str(project_id) in (r.grants or [])]

    async def for_project(
        self, integration_id: uuid.UUID, project_id: uuid.UUID
    ) -> Integration:
        row = await self._session.get(Integration, integration_id)
        if row is None:
            raise NotFoundError(say("integrationNotFound"))
        if str(project_id) not in (row.grants or []):
            raise ForbiddenError(
                say("integrationNotSharedWithProject", owner=row.owner_handle)
            )
        return row

    async def _check(self, row: Integration) -> None:
        row.last_checked_at = _now()
        if row.provider == "mail":
            await asyncio.to_thread(guard_mail_hosts, row.config)
        try:
            if row.provider == "mail":
                await asyncio.to_thread(mail.check, mail_settings(row))
            else:
                settings = await feishu_settings_for(self._session, row)
                # The owner's authorization is proved by refreshing it: a token
                # still in its two hours says nothing of whether it will be
                # renewed, and the refresh is what stops working when it lapses.
                if settings.user_access_token or settings.refresh_token:
                    settings = await _renewed(self._session, row, lambda _: True)
                await FeishuClient(settings).tenant_token()
        except IntegrationError as exc:
            row.status = {
                "auth_failed": "auth_failed",
                "credentials_invalid": "auth_failed",
                "unreachable": "unreachable",
            }.get(exc.kind, "error")
            row.last_error = str(exc.args[0])
            raise
        row.status = "ok"
        row.last_error = ""

    async def connect(
        self,
        *,
        owner_user_id: int,
        owner_handle: str,
        provider: str,
        label: str,
        config: dict,
        secret: dict,
        grants: list[str],
    ) -> Integration:
        row = Integration(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            provider=provider,
            label=label,
            config=config,
            grants=[str(g) for g in grants],
            status="ok",
        )
        seal(row, secret)
        await self._check(row)
        self._session.add(row)
        await self._session.flush()
        return row

    async def connect_feishu(
        self, *, owner_user_id: int, owner_handle: str, team_id: int
    ) -> Integration:
        """The member's Feishu connection through a team's app, before they
        authorize: the row the callback puts their tokens in.

        Only a team they are on, and only one that has an app. The row names the
        team and copies nothing of the app; picking the same team again returns
        the same row. No call is made here: the app was proved when it was saved,
        and the authorization-code exchange proves it again a moment later.
        """
        teams = team_service(self._session)
        team = await teams.get_team(team_id)
        if team is None or not await teams.is_team_member(team_id, owner_user_id):
            raise NotFoundError(say("integrationNotFound"))
        if await self._session.get(TeamFeishuApp, team_id) is None:
            raise ValidationError(say("feishuTeamAppMissing", team=team.name))
        existing = await TeamFeishuAppService(self._session).connection_for(
            owner_user_id, team_id
        )
        if existing is not None:
            return existing
        row = Integration(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            provider="feishu",
            label=team.name,
            config={"team_id": team_id, "folders": []},
            grants=[],
            status="ok",
        )
        seal(row, {})
        # Two connects at once meet at ``uq_integrations_owner_feishu_team``:
        # the one that lost takes the row the other made.
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await TeamFeishuAppService(self._session).connection_for(
                owner_user_id, team_id
            )
            if existing is None:
                raise
            return existing
        return row

    async def update(
        self, row: Integration, *, grants=None, config=None, secret=None, label=None
    ):
        if grants is not None:
            row.grants = [str(g) for g in grants]
        if label:
            row.label = label
        recheck = False
        if config:
            row.config = {**row.config, **config}
            recheck = True
        if secret:
            seal(row, {**unseal(row), **secret})
            recheck = True
        if recheck:
            # Flushed first: checking a Feishu row may lock and read it again
            # (``_renewed``), which would otherwise drop these changes.
            await self._session.flush()
            await self._check(row)
        await self._session.flush()
        return row

    async def recheck(self, row: Integration) -> Integration:
        try:
            await self._check(row)
        except IntegrationError:
            pass
        await self._session.flush()
        return row

    # ── mail ───────────────────────────────────────────────────────────────

    async def _mail(self, row: Integration, fn, *args, **kwargs):
        if row.provider != "mail":
            raise ValidationError(say("integrationNotMail"))
        await asyncio.to_thread(guard_mail_hosts, row.config)
        try:
            return await asyncio.to_thread(fn, mail_settings(row), *args, **kwargs)
        except IntegrationError as exc:
            if exc.kind == "auth_failed":
                row.status, row.last_error = "auth_failed", str(exc.args[0])
                await self._session.flush()
            raise

    async def search(self, row: Integration, **query) -> list[dict]:
        since = query.pop("since", None)
        if since:
            try:
                query["since"] = date.fromisoformat(since)
            except ValueError as exc:
                raise ValidationError(say("mailSinceFormat")) from exc
        return await self._mail(row, mail.search, **query)

    async def read(self, row: Integration, uid: str, folder: str) -> dict:
        message = await self._mail(row, mail.read, uid, folder=folder)
        return {**message, "source": f"{row.label} · {folder} · UID {uid}"}

    async def attachment(self, row: Integration, uid: str, index: int, folder: str):
        return await self._mail(row, mail.attachment, uid, index, folder=folder)

    def _attachment_bytes(
        self, project_id: uuid.UUID, room_id: uuid.UUID, path: str
    ) -> bytes:
        try:
            return self._room_files.read_room_file(project_id, room_id, path)
        except ValidationError as exc:
            raise ValidationError(say("mailAttachmentNotInRoom", path=path)) from exc

    def _message(
        self, row: Integration, draft: MailDraft, files: list[tuple[str, bytes]]
    ) -> EmailMessage:
        return mail.compose(
            sender=row.config.get("address") or row.config["username"],
            to=draft.to,
            cc=draft.cc,
            subject=draft.subject,
            body=draft.body,
            attachments=files,
            in_reply_to=draft.in_reply_to,
            message_id=draft.message_id,
        )

    async def draft(
        self,
        row: Integration,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        by: str,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[str],
        in_reply_to: str | None,
    ) -> MailDraft:
        to, cc = mail.addresses(to), mail.addresses(cc)
        if not to:
            raise ValidationError(say("mailRecipientRequired"))
        files: list[tuple[str, bytes]] = []
        recorded = []
        for path in attachments:
            data = self._attachment_bytes(project_id, room_id, path)
            name = path.rsplit("/", 1)[-1]
            files.append((name, data))
            recorded.append(
                {
                    "path": path,
                    "name": name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        if sum(len(d) for _n, d in files) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(say("mailAttachmentsTooLarge"))
        draft = MailDraft(
            id=uuid.uuid4(),
            integration_id=row.id,
            project_id=project_id,
            topic_id=room_id,
            created_by=by,
            to=to,
            cc=cc,
            subject=subject,
            body=body,
            attachments=recorded,
            in_reply_to=in_reply_to,
            message_id="",
            status="drafted",
        )
        message = self._message(row, draft, files)
        draft.message_id = message["Message-ID"]
        folder = await self._mail(row, mail.append_draft, message)
        draft.error = ""
        self._session.add(draft)
        await self._session.flush()
        draft.attachments = recorded
        row.config = {**row.config, "drafts_folder": folder}
        return draft

    async def get_draft(self, draft_id: uuid.UUID) -> MailDraft:
        draft = await self._session.get(MailDraft, draft_id)
        if draft is None:
            raise NotFoundError(say("mailDraftNotFound"))
        return draft

    async def send(self, draft: MailDraft, *, owner_user_id: int, by: str) -> dict:
        row = await self.get_owned(draft.integration_id, owner_user_id)
        if draft.status != "drafted":
            raise ValidationError(say("mailDraftNotSendable", status=draft.status))
        files: list[tuple[str, bytes]] = []
        assert draft.topic_id is not None
        for item in draft.attachments:
            data = self._attachment_bytes(
                draft.project_id, draft.topic_id, item["path"]
            )
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                # The teammate who drafted it is the one to ask for a new
                # draft, called by its name: the refusal is read in a toast
                # on 「我的连接」 as well as on the room's card.
                raise ValidationError(
                    say(
                        "mailAttachmentChanged",
                        name=item["name"],
                        agent=await self._teammates.name_of(
                            draft.project_id, draft.created_by
                        ),
                    )
                )
            files.append((item["name"], data))
        message = self._message(row, draft, files)
        try:
            refused = await self._mail(row, mail.send, message)
        except IntegrationError as exc:
            draft.status, draft.error = "failed", str(exc.args[0])
            await self._session.flush()
            raise
        draft.status = "sent"
        draft.sent_at = _now()
        draft.confirmed_by = by
        draft.error = f"服务器拒收：{', '.join(refused)}" if refused else ""
        notes = []
        try:
            sent_folder = await self._mail(row, mail.save_sent, message)
            if sent_folder:
                notes.append(f"已存一份到「{sent_folder}」")
        except IntegrationError as exc:
            notes.append(f"没能存到已发送（{exc.args[0]}）")
        drafts_folder = row.config.get("drafts_folder")
        if drafts_folder:
            try:
                removed = await self._mail(
                    row, mail.remove_by_message_id, drafts_folder, draft.message_id
                )
                notes.append("已从草稿箱移除" if removed else "草稿箱里没找到这封草稿")
            except IntegrationError as exc:
                notes.append(f"没能从草稿箱移除（{exc.args[0]}）")
        await self._session.flush()
        return {"draft": draft_view(draft), "refused": refused, "notes": notes}

    async def discard(self, draft: MailDraft, *, owner_user_id: int) -> MailDraft:
        row = await self.get_owned(draft.integration_id, owner_user_id)
        if draft.status != "drafted":
            raise ValidationError(say("mailDraftDiscardUnsentOnly"))
        folder = row.config.get("drafts_folder")
        if folder:
            await self._mail(row, mail.remove_by_message_id, folder, draft.message_id)
        draft.status = "discarded"
        await self._session.flush()
        return draft

    async def drafts_of(
        self, owner_user_id: int, status: str | None
    ) -> list[MailDraft]:
        query = (
            select(MailDraft)
            .join(Integration, Integration.id == MailDraft.integration_id)
            .where(Integration.owner_user_id == owner_user_id)
            .order_by(MailDraft.created_at.desc())
        )
        if status:
            query = query.where(MailDraft.status == status)
        return list(await self._session.scalars(query.limit(100)))

    # ── Feishu ─────────────────────────────────────────────────────────────

    async def feishu(self, row: Integration, action):
        if row.provider != "feishu":
            raise ValidationError(say("integrationNotFeishu"))
        held = row.secret
        try:
            settings = await _renewed(self._session, row, _access_lapses)
        except IntegrationError as exc:
            await self._keep_apart(row, exc, held)
            raise
        client = FeishuClient(settings)
        try:
            result = await action(client)
        except IntegrationError as exc:
            keep_feishu_tokens(row, settings)
            await self._keep_apart(row, exc, held)
            raise
        keep_feishu_tokens(row, settings)
        await self._session.flush()
        return result

    async def _keep_apart(
        self, row: Integration, exc: IntegrationError, held: str
    ) -> None:
        """Keep what a failing call learned although the request that made it
        is rolled back: the tokens a refresh just issued (the one it spent is
        gone at Feishu) and, when only the owner can mend it, the failure.

        This request's own transaction is ended first — it may hold this row's
        lock (``_renewed``) — and the write goes through a session of its own on the same engine.
        """
        values: dict = {}
        if row.secret != held:
            values |= {"secret": row.secret, "config": dict(row.config)}
        if exc.kind in NEEDS_OWNER:
            values |= {
                "status": "auth_failed",
                "last_error": str(exc.args[0]),
                "last_checked_at": _now(),
            }
        if not values:
            return
        row_id = row.id
        engine = self._session.bind
        await self._session.rollback()
        async with AsyncSession(engine, expire_on_commit=False) as apart:
            await apart.execute(
                update(Integration).where(Integration.id == row_id).values(**values)
            )
            await apart.commit()
