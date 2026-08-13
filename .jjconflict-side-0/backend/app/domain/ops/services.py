"""Operation-request service — read the manifests on a topic's branch.

Read-only by construction. This is the whole "只登记不执行" surface on the
backend side: it finds the request files, validates them, and hands the card its
seven questions. There is no dispatch path here and there must not be one until
the execution step is designed (starting from a read-only operation).
"""

import uuid

from app.domain.ops.manifest import (
    REQUESTS_DIR,
    ManifestError,
    validate_manifest,
)
from app.domain.ops.schemas import OperationCardFace, OperationRequestOut
from app.domain.workspace import service as ws

_PREFIX = f"{REQUESTS_DIR}/"


class OperationRequestService:
    def __init__(self, project_id: uuid.UUID, topic_id: uuid.UUID | None = None):
        self.project_id = project_id
        self.topic_id = topic_id

    def _manifest_paths(self) -> list[str]:
        return sorted(
            entry["path"]
            for entry in ws.list_files(self.project_id, topic_id=self.topic_id)
            if str(entry["path"]).startswith(_PREFIX)
            and str(entry["path"]).endswith(".yaml")
        )

    def list_requests(self) -> list[OperationRequestOut]:
        results: list[OperationRequestOut] = []
        for path in self._manifest_paths():
            text = ws.read_file(self.project_id, path, topic_id=self.topic_id)
            try:
                validated = validate_manifest(path, text)
            except ManifestError as exc:
                results.append(
                    OperationRequestOut(path=path, valid=False, issues=exc.issues)
                )
                continue
            results.append(
                OperationRequestOut(
                    path=path,
                    valid=True,
                    face=OperationCardFace.model_validate(validated.card_face()),
                )
            )
        return results


__all__ = ["OperationRequestService"]
