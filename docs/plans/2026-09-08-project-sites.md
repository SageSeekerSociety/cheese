# Project sites

## Goal

Publish a private static website from an accepted project revision, independently
of topic previews and development machines.

## Design

Each project owns one Site. A release records its accepted Git commit, source
directory, publisher, and immutable file snapshot under the persistent workspace
root. Publishing writes the complete snapshot before switching the current release.
Project members can read it; project managers publish it.

The project delivery page lists static website directories in the accepted
revision and displays that revision before publication. A changed revision requires
another review. Topic previews link to this project page without publishing their
working branches. Source projects requiring a build and applications requiring a
backend are outside this initial static hosting implementation.

Content runs on a separate origin for each project. Deployment must use a content
domain separate from the platform's registrable domain, without platform cookies
or credentialed CORS. Local tests use localhost hosts. The platform issues a
30-second read-only grant bound to the viewer, project, content host and purpose.
It travels in a POST body, is exchanged for a host-only HttpOnly cookie, and is
removed before project content loads. HTTPS cookies use the __Host- prefix.
Site credentials use a separate signing key and cannot authenticate platform APIs.

Every content request checks current project membership. Private responses use
no-store and disallow Service Workers. Revocation prevents future server reads;
already downloaded content cannot be recalled. The content host never routes to
platform APIs. Only regular files from the selected Git subtree enter the snapshot;
symlinks and traversal are rejected. No project build scripts run on the backend.

## Implementation and verification

1. Add workspace reads pinned to a Git commit, Site/release models and migration,
   snapshot storage, and project Site APIs. Test draft exclusion, asset bytes,
   stale revisions, path boundaries, and failed updates retaining the published Site.
2. Add isolated host routing and scoped grants. Test cross-project grants,
   platform-token rejection, private access, and membership revocation.
3. Add project delivery UI and Site opening route. Test publication of the displayed
   revision, error states, project switching, and permissions.
4. Run tests, migration checks, build and browser verification on Mac mini. Verify a
   real published sample after its development workspace is unavailable.
5. Review the diff and open a PR. Prepare DNS/gateway activation for concrete
   approval before changing the internet-facing boundary.

## Files

- `backend/app/domain/site/{models,services,hosting}.py`
- `backend/app/api/routes/{project_sites,site_sessions}.py`
- `backend/app/domain/workspace/service.py`
- `backend/app/{main,models}.py`, `backend/app/core/config.py`
- `backend/alembic/versions/` and `backend/alembic/HEAD`
- `frontend/src/views/{ProjectDeliveryView,SiteOpenView}.vue`
- `frontend/src/router/{index,workspaceRoutes}.ts`
- `frontend/src/components/{TopicSidebar,panels/PanelPreview}.vue`
- `frontend/src/{api,cx_types}.ts`, corresponding tests and deployment documentation
