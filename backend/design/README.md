# Cheese API Design

This directory contains the OpenAPI 3.0 specification for the Cheese Community API, organized by domain.

## Structure

```
design/
├── openapi.yaml           # Main entry point
├── common/                # Shared components
│   ├── parameters.yaml    # Common path/query parameters
│   ├── responses.yaml     # Common response schemas
│   └── security.yaml      # Security schemes
└── domains/               # Domain-specific specs
    ├── user/              # User management, auth, 2FA
    ├── question/          # Questions
    ├── answer/            # Answers
    ├── comment/           # Comments
    ├── topic/             # Topics/tags
    ├── material/          # Materials and bundles
    ├── knowledge/         # Knowledge base
    ├── group/             # Legacy groups
    ├── team/              # Team management
    ├── project/           # Projects
    ├── space/             # Learning spaces
    ├── task/              # Tasks with AI advice
    ├── avatar/            # User avatars
    ├── attachment/        # File attachments
    ├── notification/      # Notifications
    ├── discussion/        # Threaded discussions
    └── llm/               # AI/LLM features
```

## Usage

### Validate Spec

```bash
# Using redocly
npx @redocly/cli lint design/openapi.yaml

# Using swagger-cli
npx swagger-cli validate design/openapi.yaml
```

### Bundle into Single File

```bash
# Using redocly
npx @redocly/cli bundle design/openapi.yaml -o design/bundled.yaml

# Using swagger-cli
npx swagger-cli bundle design/openapi.yaml -o design/bundled.yaml
```

### Generate Documentation

```bash
# Generate HTML docs
npx @redocly/cli build-docs design/openapi.yaml -o docs/api.html

# Serve interactive docs
npx @redocly/cli preview-docs design/openapi.yaml
```

### Compare with Implementation

FastAPI auto-generates OpenAPI from code. To compare:

```bash
# Export from running server
curl -s http://localhost:8081/openapi.json > /tmp/implemented.json

# Convert designed spec to JSON
npx @redocly/cli bundle design/openapi.yaml --ext json -o /tmp/designed.json

# Compare (requires yq)
diff <(jq -S . /tmp/designed.json) <(jq -S . /tmp/implemented.json)
```

## Adding New Endpoints

1. Create or update the domain's `paths.yaml` and `schemas.yaml`
2. Add path reference to `openapi.yaml`
3. Run validation
4. Implement in FastAPI

### Addressing

The spec's `servers` mirror what the app publishes (`backend/app/main.py`): the
API is reachable at `/api` on the app origin, never at a bare backend port.
Anything generated from this spec inherits that base, so keep the two `servers`
lists identical — the full reasoning lives in `docs/api-conventions.md`.

## Spec-First Workflow

For new features, follow spec-first approach:

1. **Design**: Write spec in domain files
2. **Review**: PR review on spec changes
3. **Implement**: Write FastAPI code matching spec
4. **Validate**: Compare auto-generated vs designed spec
5. **Test**: Integration tests verify the contract
