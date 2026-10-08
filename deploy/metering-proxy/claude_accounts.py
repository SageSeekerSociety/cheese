"""Sticky Claude account selection with durable, single-probe cooldowns.

Also publishes the pool's state as ``accounts.json`` next to the ledger, for the
admin board to read — see ``snapshot``. The pool exists only on this box, and
the backend has no route to it.
"""

import json
import logging
import math
import os
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from pathlib import Path

from cheese_billing_core import PlatformCredential

log = logging.getLogger("cheese.accounts")


class ClaudeAccounts:
    def __init__(self, primary: PlatformCredential, *, now=time.time, snapshot_path=None):
        self.primary = primary
        self.now = now
        self.path = primary.path.parent / "cooldowns.json"
        # Where to publish the pool for the backend to read; None = nowhere,
        # which is what tests that only exercise selection pass.
        self.snapshot_path = Path(snapshot_path) if snapshot_path else None
        self.credentials = {"primary": primary}
        self.affinity = OrderedDict()
        self.cursor = 0
        self.probes = {}
        try:
            self.state = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.state = {}

    def accounts(self):
        found = {"primary": self.primary}
        root = self.primary.path.parent / "accounts"
        if root.exists():
            for directory in sorted(root.iterdir()):
                if directory.is_dir() and (directory / "credential").is_file():
                    name = directory.name
                    found[name] = self.credentials.get(name) or PlatformCredential(
                        directory / "credential"
                    )
        self.credentials = found
        return found

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(self.state, output, allow_nan=False)
        os.replace(temporary, self.path)
        # Every state change goes through here, so the published copy can never
        # lag a cooldown by more than one write. Billing calls `snapshot` too:
        # `retry_after` is a countdown and would otherwise be counted from the
        # last cooldown change, however long ago that was.
        self.snapshot()

    def snapshot(self):
        """Publish the pool where the backend can read it — beside the ledger.

        The accounts and their cooldowns exist only on this box, inside this
        process. The backend has no route here, but it already reads the
        ledger's directory (mounted for usage ingest), so one small JSON file
        next to `usage.jsonl` is the whole channel.

        Atomic (temp file + `os.replace`): the reader picks it up at an
        arbitrary moment, and a torn file would read as an empty pool. A failed
        write is logged, never raised — this is a report, and losing it must not
        cost a model call.
        """
        if self.snapshot_path is None:
            return
        accounts = []
        for name in self.accounts():
            status = self.state.get(name)
            until = status.get("until") if status else None
            if status is None:
                state = "available"
            elif until is None:
                # A spent allowance with no reset to wait for: an operator has
                # to clear it, time will not.
                state = "disabled"
            else:
                state = "cooling"
            accounts.append(
                {
                    "name": name,
                    "state": state,
                    "until": until,
                    "failures": (status or {}).get("failures", 0),
                }
            )
        document = {
            "written_at": self.now(),
            "retry_after": self.retry_after(),
            "accounts": accounts,
        }
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.snapshot_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(document, output, allow_nan=False)
            os.replace(temporary, self.snapshot_path)
        except OSError as error:
            log.warning("could not publish the Claude account snapshot: %s", error)

    def select(self, session, excluded=(), *, request_id=None):
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        available, recovering = [], []
        for name, credential in self.accounts().items():
            if name in excluded or name in self.probes:
                continue
            token, _ = credential.token()
            if not token:
                continue
            status = self.state.get(name)
            if status:
                if status["until"] is None or status["until"] > self.now():
                    continue
                recovering.append(name)
            else:
                available.append(name)
        # A real request, never a polling loop, tests one cooled account.
        if self.affinity.get(session) in available:
            name = self.affinity[session]
        elif recovering:
            name = recovering[0]
            self.probes[name] = request_id
        elif available:
            name = available[self.cursor % len(available)]
            self.cursor += 1
        else:
            return None
        self.affinity[session] = name
        self.affinity.move_to_end(session)
        if len(self.affinity) > 10000:
            self.affinity.popitem(last=False)
        return name, self.credentials[name]

    def reject(self, name, headers, body):
        """Quarantine a 429 account without inferring a reset from its status."""
        previous = self.state.get(name, {})
        failures = previous.get("failures", 0) + 1
        headers = {k.lower(): v for k, v in headers.items()}
        now = self.now()
        reset = None
        retry_after = headers.get("retry-after", "")
        try:
            reset = now + max(0, float(retry_after))
        except ValueError:
            try:
                reset = parsedate_to_datetime(retry_after).timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
        # This is the reset for the binding unified limit, not an unrelated
        # weekly window that may still have capacity.
        try:
            unified = float(headers.get("anthropic-ratelimit-unified-reset", ""))
            if math.isfinite(unified) and unified > now:
                reset = max(reset or 0, unified)
        except ValueError:
            pass
        if reset is not None and not math.isfinite(reset):
            reset = None
        text = body.decode("utf-8", errors="replace").lower()
        quota = any(
            word in text
            for word in (
                "usage limit",
                "weekly limit",
                "5-hour limit",
                "monthly spend limit",
                "quota",
                "credit balance",
            )
        )
        if reset is None:
            # An explicit spent quota with no reset stays disabled until an
            # operator resets it. Unknown 429s back off instead of flapping.
            reset = None if quota else now + min(3600, 300 * 2 ** min(failures - 1, 4))
        else:
            reset = max(now + 1, reset)
        self.state[name] = {
            "until": reset,
            "failures": failures,
        }
        self.probes.pop(name, None)
        self._save()
        log.warning("Claude account %s cooling until %s after 429", name, reset)

    def accepted(self, name, request_id=None):
        if name in self.probes and self.probes[name] == request_id:
            self.probes.pop(name)
            self.state.pop(name, None)
            self._save()
            log.info("Claude account %s recovered", name)

    def release(self, name, request_id=None):
        if name in self.probes and self.probes[name] == request_id:
            self.probes.pop(name)
            self.state[name]["until"] = self.now() + 300
            self._save()

    def retry_after(self):
        deadlines = [s["until"] for s in self.state.values() if s["until"] is not None]
        return max(1, math.ceil(min(deadlines) - self.now())) if deadlines else None
