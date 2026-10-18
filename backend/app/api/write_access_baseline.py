# ruff: noqa: E501 — one route per line, path templates unbroken
"""Write routes that predated write-access declarations, frozen as they were.

Each entry is (method, path template) of a route that declares nothing and is
let through by `app.api.write_access.seal` anyway: the gate in front of them was
open before declarations existed, and each still authorizes its caller in its
handler. The list only shrinks. Declare a route (usually ``ROUTE_DECIDES``) and
delete its line; tests/unit/test_write_routes_declare_their_callers.py fails on
an entry that is declared, gone or renamed, and on a list that grew.
"""

UNDECLARED: frozenset[tuple[str, str]] = frozenset(
    {
        ("DELETE", "/connector/my/devices/{device_id}"),
        ("DELETE", "/documents/{document_id}"),
        ("DELETE", "/invitations/{invitation_id}"),
        ("DELETE", "/me/integrations/{integration_id}"),
        ("DELETE", "/notifications/{notification_id}"),
        ("DELETE", "/questions/{question_id}"),
        ("DELETE", "/routines/{routine_id}"),
        ("DELETE", "/sandbox/forge/{project_id}/{path:path}"),
        ("DELETE", "/skills/{skill_id}"),
        ("DELETE", "/topics/{topic_id}/members/{handle}"),
        ("DELETE", "/users/invite-codes/{code_id}"),
        ("DELETE", "/users/me/team-requests/{requestId}"),
        ("DELETE", "/users/me/understanding/{entry_id}"),
        ("PATCH", "/blocks/{block_id}"),
        ("PATCH", "/connector/my/devices/{device_id}"),
        ("PATCH", "/integrations/{integration_id}/feishu/docs/{document_id}"),
        ("PATCH", "/me/integrations/{integration_id}"),
        ("PATCH", "/projects/{project_id}/artifacts/{artifact_id}"),
        ("PATCH", "/routines/{routine_id}"),
        ("PATCH", "/sandbox/forge/{project_id}/{path:path}"),
        ("PATCH", "/skills/{skill_id}"),
        ("PATCH", "/topics/{topic_id}/task"),
        ("POST", "/accept-cards/{card_id}/accept"),
        ("POST", "/attachments"),
        ("POST", "/backend-errors"),
        ("POST", "/comments/{commentId}/attitudes"),
        ("POST", "/connector/auth/device/poll"),
        ("POST", "/documents/{document_id}/agent"),
        ("POST", "/fetch"),
        ("POST", "/groups"),
        ("POST", "/integrations/{integration_id}/feishu/docs"),
        ("POST", "/invitations/{invitation_id}/respond"),
        ("POST", "/llm/admission"),
        ("POST", "/material-bundles"),
        ("POST", "/me/integrations/feishu"),
        ("POST", "/memory/files/delete"),
        ("POST", "/office-editor/callback/{link}"),
        ("POST", "/projects"),
        ("POST", "/projects/{project_id}/git/tasks/{task_id}"),
        ("POST", "/projects/{project_id}/invitations"),
        ("POST", "/projects/{project_id}/site-session"),
        ("POST", "/push/subscriptions/delete"),
        ("POST", "/routine-runs/{run_id}/report"),
        ("POST", "/sandbox/forge/{project_id}/{path:path}"),
        ("POST", "/skills/{skill_id}/confirm"),
        ("POST", "/space-applications/{space_id}/resubmit"),
        ("POST", "/topics"),
        ("POST", "/topics/{topic_id}/execution/session-{resource_id}"),
        ("POST", "/topics/{topic_id}/feedback-proposals"),
        ("POST", "/topics/{topic_id}/preview-session"),
        ("POST", "/users"),
        ("POST", "/users/me/team-invitations/{invitationId}/accept"),
        ("POST", "/users/oauth/bind"),
        ("PUT", "/documents/{document_id}"),
        ("PUT", "/internal/collab/documents/{name}"),
        ("PUT", "/notifications/preferences"),
        ("PUT", "/projects/{project_id}/agents/{agent_id}"),
        ("PUT", "/projects/{project_id}/file"),
        ("PUT", "/projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_sha}"),
        ("PUT", "/projects/{project_id}/slug"),
        ("PUT", "/push/subscriptions"),
        ("PUT", "/sandbox/forge/{project_id}/{path:path}"),
        ("PUT", "/topics/{topic_id}/description"),
        ("PUT", "/users/me/language"),
    }
)
