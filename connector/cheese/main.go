// cheese — a thin CLI over the Cheese backend for agents (and humans).
//
// It embeds Restish (github.com/rest-sh/restish) as a library and points it at
// the backend's live OpenAPI. Every run fetches the spec fresh and builds the
// command tree from it, so when the server's API changes the CLI changes with
// it — no rebuild, no reinstall, no manual "update" step (like opening a web
// page whose content changed). The client carries zero business logic.
//
// Config (env):
//
//	CHEESE_API    base URL of the backend (default http://localhost:8080)
//	CHEESE_TOKEN  session/login token; sent as `Authorization: Bearer <token>`
//	              on every request to CHEESE_API's host (an agent's "login").
//
// Usage:
//
//	cheese                 # list every operation (from the live OpenAPI)
//	cheese <operation> -h  # help for one operation, generated from the spec
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/iancoleman/strcase"
	"github.com/rest-sh/restish/cli"
	"github.com/rest-sh/restish/openapi"
	"github.com/spf13/viper"
)

var version = "dev"

const apiName = "cheese"

// bearerInjector adds the agent's session token to every request aimed at the
// backend host. It sits at the bottom of Restish's transport stack so it covers
// both the spec fetch and the actual API calls, without depending on Restish's
// per-API auth config.
type bearerInjector struct {
	base  http.RoundTripper
	host  string
	token string
}

func (b *bearerInjector) RoundTrip(req *http.Request) (*http.Response, error) {
	if b.token != "" && req.URL.Host == b.host && req.Header.Get("Authorization") == "" {
		req = req.Clone(req.Context())
		req.Header.Set("Authorization", "Bearer "+b.token)
	}
	resp, err := b.base.RoundTrip(req)
	if err != nil || resp == nil {
		return resp, err
	}
	// Sanitize the OpenAPI spec on the way in so a server-side wart (e.g. two
	// params that collapse to the same CLI flag) can never crash the CLI. This
	// is generic spec-hygiene, not business logic — the client stays thin and
	// simply tolerates whatever the server serves, like a browser tolerating
	// imperfect HTML.
	if req.URL.Host == b.host && resp.StatusCode == http.StatusOK &&
		strings.HasSuffix(req.URL.Path, "openapi.json") {
		if data, e := io.ReadAll(resp.Body); e == nil {
			_ = resp.Body.Close()
			data = dedupeFlagParams(data)
			resp.Body = io.NopCloser(bytes.NewReader(data))
			resp.ContentLength = int64(len(data))
			resp.Header.Set("Content-Length", strconv.Itoa(len(data)))
		}
	}
	return resp, err
}

// dedupeFlagParams drops parameters within an operation whose names collapse to
// the same CLI flag (Restish uses strcase.ToDelimited(name,'-')), keeping the
// first. Without this, an operation declaring both `sortBy` and `sort_by` would
// register the `--sort-by` flag twice and panic.
func dedupeFlagParams(data []byte) []byte {
	var doc map[string]any
	if json.Unmarshal(data, &doc) != nil {
		return data
	}
	paths, _ := doc["paths"].(map[string]any)
	for _, pv := range paths {
		methods, _ := pv.(map[string]any)
		for _, mv := range methods {
			op, ok := mv.(map[string]any)
			if !ok {
				continue
			}
			params, ok := op["parameters"].([]any)
			if !ok {
				continue
			}
			seen := map[string]bool{}
			kept := make([]any, 0, len(params))
			for _, pp := range params {
				pm, ok := pp.(map[string]any)
				if !ok {
					kept = append(kept, pp)
					continue
				}
				name, _ := pm["name"].(string)
				in, _ := pm["in"].(string)
				// query/header/cookie params all share one --flag namespace;
				// path params are positional. Dedupe within each bucket.
				bucket := "flag"
				if in == "path" {
					bucket = "path"
				}
				key := bucket + "\x00" + strcase.ToDelimited(name, '-')
				if seen[key] {
					continue
				}
				seen[key] = true
				kept = append(kept, pp)
			}
			op["parameters"] = kept
		}
	}
	if out, e := json.Marshal(doc); e == nil {
		return out
	}
	return data
}

func apiBase() string {
	base := os.Getenv("CHEESE_API")
	if base == "" {
		base = "http://localhost:8080"
	}
	return strings.TrimRight(base, "/")
}

// selfConfigure writes an apis.json pointing Restish at the backend, derived
// entirely from env. This is not a spec — just "where the API is" — so the CLI
// still discovers the actual operations live. Regenerated every run so a changed
// CHEESE_API takes effect with no manual step. Returns the config dir.
func selfConfigure(base string) string {
	dir := os.Getenv("CHEESE_CONFIG_DIR")
	if dir == "" {
		cfgBase, err := os.UserConfigDir()
		if err != nil {
			cfgBase = os.TempDir()
		}
		dir = filepath.Join(cfgBase, apiName)
		os.Setenv("CHEESE_CONFIG_DIR", dir)
	}
	_ = os.MkdirAll(dir, 0o700)

	apis := map[string]any{
		"$schema": "https://rest.sh/schemas/apis.json",
		apiName: map[string]any{
			"base":       base,
			"spec_files": []string{base + "/openapi.json"},
		},
	}
	if data, err := json.MarshalIndent(apis, "", "  "); err == nil {
		_ = os.WriteFile(filepath.Join(dir, "apis.json"), data, 0o600)
	}
	return dir
}

func main() {
	base := apiBase()
	selfConfigure(base)

	// Inject the agent's login token into every backend-bound request.
	if u, err := url.Parse(base); err == nil {
		http.DefaultTransport = &bearerInjector{
			base:  http.DefaultTransport,
			host:  u.Host,
			token: os.Getenv("CHEESE_TOKEN"),
		}
	}

	cli.Init(apiName, version)
	cli.Defaults()
	cli.AddLoader(openapi.New())

	// Restish auto-registers a bare `cheese <apiName>` stub from the config;
	// drop it since we surface every operation at the top level instead.
	for _, c := range cli.Root.Commands() {
		if c.Name() == apiName {
			cli.Root.RemoveCommand(c)
			break
		}
	}

	// Always reflect the server's current spec — never serve a stale command
	// tree from cache. This is what makes "the CLI follows the API" true.
	viper.Set("rsh-no-cache", true)

	// Fetch the live OpenAPI and hydrate every operation as a top-level command
	// (`cheese <operation>`), so the agent uses the exact same endpoints a human
	// does. Non-fatal on failure: generic HTTP verbs still work.
	if _, err := cli.Load(base+"/", cli.Root); err != nil {
		cli.LogWarning("could not load API from %s: %v", base, err)
	}

	if err := cli.Run(); err != nil {
		os.Exit(1)
	}
	os.Exit(cli.GetExitCode())
}
