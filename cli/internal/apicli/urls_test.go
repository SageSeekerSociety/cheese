package apicli

// How `cheese api` turns the server's published schema into request URLs.
//
// Restish (the engine under `cheese api`) reads the spec's `servers` list and
// takes the first URL that starts with "/" as a base path, then builds every
// operation URL as scheme://host-of-base + that base path + the operation's
// path (openapi.getBasePath / loadOpenAPI3 in rest-sh/restish). The backend
// publishes servers ["/api", "/"] (backend/app/main.py), so servers[0]
// decides the path of every URL `cheese api <op>` sends. These tests pin that
// mechanism with the loader Setup actually wires (openapi.New), fed the same
// spec shape the backend serves — because nothing else in the repo would
// notice if it moved: the CLI is a schema consumer that rebuilds itself from
// the live spec on every run.

import (
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/rest-sh/restish/openapi"
)

// A minimal spec carrying what matters here: the backend's published servers
// and two ordinary bare paths. It used to carry one path per generation, the
// 2.0 one with its own /api — #370 retired that prefix, and this literal is the
// only place in the repo that would otherwise still describe the old shape.
const specWithServers = `{
  "openapi": "3.0.3",
  "info": {"title": "CheeseX", "version": "0.1.0"},
  "servers": [
    {"url": "/api", "description": "Through the app origin"},
    {"url": "/", "description": "Straight at the backend port"}
  ],
  "paths": {
    "/topics": {
      "get": {"operationId": "list-topics", "responses": {"200": {"description": "ok"}}}
    },
    "/users/auth/login": {
      "post": {"operationId": "login", "responses": {"200": {"description": "ok"}}}
    }
  }
}`

// operationURLs loads the spec exactly the way `cheese api` does — the base is
// APIBase()+"/" (see LoadOperations → cli.Load → loader.Load) — and returns
// every operation URL restish computed.
func operationURLs(t *testing.T, base string) map[string]bool {
	t.Helper()
	baseURL, err := url.Parse(base)
	if err != nil {
		t.Fatalf("parse base %q: %v", base, err)
	}
	specURL, err := url.Parse(base + "openapi.json")
	if err != nil {
		t.Fatalf("parse spec url: %v", err)
	}
	resp := &http.Response{Body: io.NopCloser(strings.NewReader(specWithServers))}

	api, err := openapi.New().Load(*baseURL, *specURL, resp)
	if err != nil {
		t.Fatalf("load spec against %q: %v", base, err)
	}
	got := map[string]bool{}
	for _, op := range api.Operations {
		got[op.URITemplate] = true
	}
	return got
}

// The platform shape: every base the platform injects ends in /api
// (settings.sandbox_api_base, device_provider's api_base), and `cheese auth
// login <origin>/connector` stores the same. servers[0] REPLACES the base's
// path rather than appending to it, so the result addresses the origin
// correctly: exactly one /api in front of every route.
func TestOperationURLsThroughGatewayBase(t *testing.T) {
	got := operationURLs(t, "https://cheese.example.com/api/")

	for _, want := range []string{
		"https://cheese.example.com/api/topics",
		"https://cheese.example.com/api/users/auth/login",
	} {
		if !got[want] {
			t.Errorf("operation URL %q not computed; got %v", want, got)
		}
	}
}

// The direct shape: a base pointing straight at the backend port. servers[0]
// is applied regardless of the base, so the URLs still carry the /api mount —
// one segment more than a gateway-less backend answers. This is a real sharp
// edge of publishing the mount in the schema, pinned here on purpose: CHEESE_API
// must point at the app origin's /api mount (where the published server
// composes correctly), not at a bare backend port. If this test ever starts
// failing because restish picks a different server entry, the CLI's whole
// addressing story changes — re-read docs/api-conventions.md before "fixing" it.
func TestOperationURLsAgainstBareBackendPort(t *testing.T) {
	got := operationURLs(t, "http://127.0.0.1:8799/")

	for _, want := range []string{
		"http://127.0.0.1:8799/api/topics",
		"http://127.0.0.1:8799/api/users/auth/login",
	} {
		if !got[want] {
			t.Errorf("operation URL %q not computed; got %v", want, got)
		}
	}
}
