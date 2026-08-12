// `cheese api` is a schema consumer, so the server's `servers[]` decides every
// URL it sends. Restish picks the first server whose URL starts with "/" as the
// base path and prepends it to each operation's path; with no such server it
// falls back to the path of the configured base. Those two rules are the whole
// contract between backend/app/main.py's `servers=[…]` and this binary — and
// they are easy to get wrong in a way nothing else in the repo would notice,
// because the resulting URL is well-formed and simply arrives somewhere else.
//
// The cases below are the bases this CLI is actually handed in production, and
// they are asserted end-to-end through the same loader Setup registers.
package apicli

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"github.com/rest-sh/restish/openapi"
)

// Two operations, one from each generation of the fused API: a 1.0 router
// declares a bare path, a 2.0 router carries its own `/api`. Trimmed to the
// fields the loader reads.
const specWithServers = `{
  "openapi": "3.1.0",
  "info": {"title": "CheeseX", "version": "0.1.0"},
  "servers": [
    {"url": "/api", "description": "Through the app origin"},
    {"url": "/", "description": "Straight at the backend port"}
  ],
  "paths": {
    "/users/auth/login": {"post": {"operationId": "login",
      "responses": {"200": {"description": "ok"}}}},
    "/api/topics": {"get": {"operationId": "listTopics",
      "responses": {"200": {"description": "ok"}}}}
  }
}`

// operationURLs loads spec through Restish's OpenAPI loader — the one
// Setup installs via cli.AddLoader — and returns the URL each operation
// would be sent to, keyed by the command name Restish derives.
func operationURLs(t *testing.T, spec, base string) map[string]string {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(spec))
		}))
	defer server.Close()

	entrypoint, err := url.Parse(base)
	if err != nil {
		t.Fatalf("bad base %q: %v", base, err)
	}
	specURL, err := url.Parse(server.URL + "/openapi.json")
	if err != nil {
		t.Fatalf("bad spec url: %v", err)
	}
	resp, err := http.Get(specURL.String())
	if err != nil {
		t.Fatalf("fetching spec: %v", err)
	}
	defer resp.Body.Close()

	api, err := openapi.New().Load(*entrypoint, *specURL, resp)
	if err != nil {
		t.Fatalf("loading spec: %v", err)
	}
	urls := map[string]string{}
	for _, op := range api.Operations {
		urls[op.Name] = op.URITemplate
	}
	return urls
}

func TestPublishedServerDecidesTheOperationURLs(t *testing.T) {
	// Every base this CLI is really given. They differ in path — the sandbox is
	// handed the gateway mount, an enrolled machine is handed the connector
	// plane (`cheesehost auth login $ORIGIN/connector`), and the built-in
	// default is the bare app origin — and the published server has to make all
	// three land on the same place, because the path of the base is discarded:
	// the operation path is absolute, so it replaces it rather than extending it.
	bases := []struct {
		name string
		base string
	}{
		{"app origin (apicli's built-in default shape)", "http://localhost:8080"},
		{"sandbox / device screen", "https://app.example.com/api"},
		{"machine enrolled by install.sh", "https://app.example.com/connector"},
	}

	for _, b := range bases {
		t.Run(b.name, func(t *testing.T) {
			urls := operationURLs(t, specWithServers, b.base)

			origin, err := url.Parse(b.base)
			if err != nil {
				t.Fatal(err)
			}
			prefix := origin.Scheme + "://" + origin.Host

			// A 1.0 path gets the mount once; a 2.0 path, which carries its own
			// `/api`, ends up doubled. Both are what the gateway expects, since
			// it strips exactly one segment before the backend sees the request.
			want := map[string]string{
				"login":       prefix + "/api/users/auth/login",
				"list-topics": prefix + "/api/api/topics",
			}
			for name, expected := range want {
				if got := urls[name]; got != expected {
					t.Errorf("%s: got %q, want %q", name, got, expected)
				}
			}
		})
	}
}

func TestWithoutAPublishedServerTheBasePathLeaksIntoEveryURL(t *testing.T) {
	// The state before the server was published, kept as a test because it is the
	// reason to keep publishing it: with no server, Restish falls back to the
	// path of whatever base the machine was configured with, so an enrolled
	// machine sent every call to /connector/... and got a 404 from the connector
	// plane, while the built-in default dropped the mount entirely and reached
	// the 1.0 route of the same name — a 200 from the wrong generation.
	const specNoServers = `{
      "openapi": "3.1.0",
      "info": {"title": "CheeseX", "version": "0.1.0"},
      "paths": {
        "/users/auth/login": {"post": {"operationId": "login",
          "responses": {"200": {"description": "ok"}}}},
        "/api/topics": {"get": {"operationId": "listTopics",
          "responses": {"200": {"description": "ok"}}}}
      }
    }`

	cases := []struct {
		base          string
		login, topics string
	}{
		{
			base:   "https://app.example.com/connector",
			login:  "https://app.example.com/connector/users/auth/login",
			topics: "https://app.example.com/connector/api/topics",
		},
		{
			base:   "http://localhost:8080",
			login:  "http://localhost:8080/users/auth/login",
			topics: "http://localhost:8080/api/topics",
		},
	}

	for _, c := range cases {
		t.Run(c.base, func(t *testing.T) {
			urls := operationURLs(t, specNoServers, c.base)
			if got := urls["login"]; got != c.login {
				t.Errorf("login: got %q, want %q", got, c.login)
			}
			if got := urls["list-topics"]; got != c.topics {
				t.Errorf("list-topics: got %q, want %q", got, c.topics)
			}
		})
	}
}

func TestAPIBasePrefersTheEnvironment(t *testing.T) {
	t.Setenv("CHEESE_API", "https://app.example.com/api/")
	if got := APIBase(); got != "https://app.example.com/api" {
		t.Errorf("APIBase() = %q, want the env value with its trailing slash trimmed", got)
	}
}
