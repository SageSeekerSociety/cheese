# Project export

`GET /projects/{project_id}/export` accepts a human bearer token with project
access and returns a tar archive. It contains the Git repository as a bundle,
library files, the published artifact catalog and accepted versions of files
from rooms the caller can access, and Markdown documents. Agent session
transcripts are not included.
Room content follows the caller's current room permissions, including private
rooms; inaccessible and deleted rooms are excluded.

`manifest.json` records repository HEAD and refs plus each exported file's byte
size and SHA-256. The library status distinguishes an existing directory from an
absent directory; an absent directory does not prove that files were never
stored. External links retain metadata only. Memory and personal profiles are
excluded. The archive captures the available persisted data while it is read;
it is not an atomic project snapshot or a backup schedule.

Missing artifact bytes abort the export.
Repositories containing Git LFS pointers or submodules are rejected because
their external objects are not included. After extracting the tar, use
`git clone repository.bundle checkout` to read the repository offline.

Authorization and offline verification are exercised by
`backend/tests/integration/test_project_export.py`, which removes the original
repository and workspace before checking the downloaded archive.
