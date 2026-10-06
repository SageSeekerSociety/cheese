"""Reference fingerprints, on disk.

A reference is only worth as much as the endpoint it was collected from: every
model this platform serves is absent from every published fingerprint set, so a
reference has to be built here, against an endpoint we are willing to call
"the real one". The artifacts record *which* endpoint that was, so a later
reader can decide whether to trust the comparison.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .stats import (
    FINGERPRINT_FORMAT_VERSION,
    PROBE_PROTOCOL,
    CellSamples,
)

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"


def _slug(model: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in model)


@dataclass
class TokenizerReference:
    base_input_tokens: int | None = None
    deltas: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"base_input_tokens": self.base_input_tokens, "deltas": self.deltas}

    @classmethod
    def from_json(cls, data: dict) -> TokenizerReference:
        return cls(
            base_input_tokens=data.get("base_input_tokens"),
            deltas={str(k): int(v) for k, v in (data.get("deltas") or {}).items()},
        )


@dataclass
class Reference:
    model: str
    source: str
    collected_at: str
    cells: dict[str, CellSamples] = field(default_factory=dict)
    samples_per_cell: int = 0
    tokenizer: TokenizerReference | None = None
    notes: str = ""

    def to_json(self) -> dict:
        return {
            "format_version": FINGERPRINT_FORMAT_VERSION,
            "protocol": PROBE_PROTOCOL,
            "model": self.model,
            "source": self.source,
            "collected_at": self.collected_at,
            "samples_per_cell": self.samples_per_cell,
            "cells": {
                cell: samples.to_json() for cell, samples in sorted(self.cells.items())
            },
            "tokenizer": self.tokenizer.to_json() if self.tokenizer else None,
            "notes": self.notes,
        }

    @classmethod
    def from_json(cls, data: dict) -> Reference:
        tokenizer = data.get("tokenizer")
        return cls(
            model=data["model"],
            source=data.get("source", ""),
            collected_at=data.get("collected_at", ""),
            samples_per_cell=int(data.get("samples_per_cell", 0)),
            cells={
                cell: CellSamples.from_json(payload)
                for cell, payload in (data.get("cells") or {}).items()
            },
            tokenizer=TokenizerReference.from_json(tokenizer) if tokenizer else None,
            notes=data.get("notes", ""),
        )


def path_for(model: str, directory: Path | None = None) -> Path:
    return (directory or REFERENCE_DIR) / f"{_slug(model)}.json"


def save(reference: Reference, directory: Path | None = None) -> Path:
    target = path_for(reference.model, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(reference.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load(model: str, directory: Path | None = None) -> Reference | None:
    target = path_for(model, directory)
    if not target.exists():
        return None
    return Reference.from_json(json.loads(target.read_text(encoding="utf-8")))


def new_reference(
    model: str, source: str, samples_per_cell: int, notes: str = ""
) -> Reference:
    return Reference(
        model=model,
        source=source,
        collected_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        samples_per_cell=samples_per_cell,
        notes=notes,
    )
