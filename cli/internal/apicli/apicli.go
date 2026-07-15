// Package apicli mounts the server's full request/response API — generated live
// from its OpenAPI via Restish — under the `cheese api` subcommand. Every
// operation the server publishes becomes `cheese api <operation>`, rebuilt each
// run from the live spec so it never goes stale and never needs a client
// update. The client carries zero business logic.
//
// The base URL and credential come from this machine's config (written by
// `cheese auth login`); the environment overrides them when set:
//
//	CHEESE_API     base URL of the server (env override)
//	CHEESE_TOKEN   credential, sent as `Authorization: Bearer <token>` (env override)
//	CHEESE_SCREEN  when set, sent as `X-Cheese-Screen: <token>` so the server can
//	               tell which screen a call originated from. The host injects this
//	               into every screen it opens, so any process inside a screen that
//	               runs `cheese api` is automatically attributed to that screen.
package apicli

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
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/spf13/viper"

	"github.com/SageSeekerSociety/cheese-backend-py/cli/internal/config"
)

const apiName = "cheese"

// Setup initializes Restish (its cobra root becomes the `cheese` root command),
// installs the bearer-token transport, and returns the `api` subcommand that
// hosts every generated operation. Operations are hydrated lazily by
// LoadOperations so non-API commands (run/install/…) never touch the network.
func Setup(version string) *cobra.Command {
	base := APIBase()
	selfConfigure(base)

	// Inject the credential (and, if we are inside a screen, the screen token)
	// into every server-bound request, at the bottom of Restish's transport
	// stack so it covers both the spec fetch and the actual calls.
	if u, err := url.Parse(base); err == nil {
		http.DefaultTransport = &bearerInjector{
			base:   http.DefaultTransport,
			host:   u.Host,
			token:  resolveToken(),
			screen: os.Getenv("CHEESE_SCREEN"),
		}
	}

	cli.Init(apiName, version)
	cli.Defaults()
	cli.AddLoader(openapi.New())

	// Drop two Restish built-ins we don't surface: the `cheese <apiName>` stub
	// it auto-registers from config, and its interactive `api` config-management
	// command (we self-configure from env, and we reuse the `api` name for the
	// live operation tree below).
	for _, c := range cli.Root.Commands() {
		if name := c.Name(); name == apiName || name == "api" {
			cli.Root.RemoveCommand(c)
		}
	}

	// Never serve a stale command tree — always reflect the server's spec.
	viper.Set("rsh-no-cache", true)

	// Keep the top level clean: hide Restish's generic REST verbs so the user
	// sees only the `cheese` service lifecycle + `api`. They still work if
	// invoked (e.g. `cheese get <url>`), just aren't listed.
	generic := map[string]bool{
		"get": true, "post": true, "put": true, "delete": true, "head": true,
		"options": true, "patch": true, "edit": true, "links": true,
		"auth-header": true, "cert": true, "completion": true,
	}
	for _, c := range cli.Root.Commands() {
		if generic[c.Name()] {
			c.Hidden = true
		}
	}

	// Rebrand the root off Restish's "generic REST client" identity, and hide its
	// low-level --rsh-* transport flags, so `cheese --help` reads like our own CLI.
	cli.Root.Use = "cheese"
	cli.Root.Short = "Connect this machine so your cheese sessions can run on it"
	// Prose only — the help template word-wraps Long, which collapses manual
	// newlines, so a hand-formatted command list here gets mashed onto one line.
	// The commands render correctly in the auto-generated sections below. Keep the
	// wording plain and reassuring — no "the server runs/drives things on your machine".
	cli.Root.Long = "cheese connects this machine to your cheese workspace so your " +
		"sessions can run here and you can use them from the web. Log in once and it " +
		"stays connected in the background. Run `cheese <command> -h` for any command; " +
		"`cheese api` calls your workspace's API."
	cli.Root.Example = ""
	cli.Root.SilenceErrors = true
	cli.Root.CompletionOptions.DisableDefaultCmd = true
	cli.Root.PersistentFlags().VisitAll(func(f *pflag.Flag) {
		if strings.HasPrefix(f.Name, "rsh-") {
			f.Hidden = true
		}
	})

	apiCmd := &cobra.Command{
		Use:   "api",
		Short: "The server's full request/response API (generated live from OpenAPI)",
		Long: "Every operation the server publishes, built fresh from its live OpenAPI\n" +
			"each run. Base URL and credential come from `cheese auth login` (or the\n" +
			"CHEESE_API / CHEESE_TOKEN env). `cheese api` lists the operations;\n" +
			"`cheese api <operation> -h` shows one operation's generated help.",
	}
	cli.Root.AddCommand(apiCmd)
	return apiCmd
}

// LoadOperations fetches the live OpenAPI and hydrates every operation as a
// child of apiCmd (`cheese api <operation>`). Non-fatal on failure — generic
// HTTP verbs still work.
func LoadOperations(apiCmd *cobra.Command) {
	base := APIBase()
	if _, err := cli.Load(base+"/", apiCmd); err != nil {
		cli.LogWarning("could not load API from %s: %v", base, err)
	}
}

// Run executes the assembled cobra root and returns Restish's exit code.
func Run() int {
	if err := cli.Run(); err != nil {
		return 1
	}
	return cli.GetExitCode()
}

// APIBase returns the server base URL: CHEESE_API if set, else this machine's
// configured base, else a localhost default.
func APIBase() string {
	if base := os.Getenv("CHEESE_API"); base != "" {
		return strings.TrimRight(base, "/")
	}
	if cfg, err := config.Load(config.DefaultPath()); err == nil && cfg.Base != "" {
		return cfg.APIBase()
	}
	return "http://localhost:8080"
}

// resolveToken returns the credential: CHEESE_TOKEN if set, else the one stored
// by `cheese auth login`.
func resolveToken() string {
	if tok := os.Getenv("CHEESE_TOKEN"); tok != "" {
		return tok
	}
	if cfg, err := config.Load(config.DefaultPath()); err == nil {
		return cfg.Token
	}
	return ""
}

// selfConfigure writes an apis.json pointing Restish at the backend, derived
// entirely from env — regenerated every run so a changed CHEESE_API takes
// effect with no manual step.
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

type bearerInjector struct {
	base   http.RoundTripper
	host   string
	token  string
	screen string
}

func (b *bearerInjector) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Host == b.host {
		if b.token != "" && req.Header.Get("Authorization") == "" {
			req = req.Clone(req.Context())
			req.Header.Set("Authorization", "Bearer "+b.token)
		}
		if b.screen != "" && req.Header.Get("X-Cheese-Screen") == "" {
			req = req.Clone(req.Context())
			req.Header.Set("X-Cheese-Screen", b.screen)
		}
	}
	resp, err := b.base.RoundTrip(req)
	if err != nil || resp == nil {
		return resp, err
	}
	// Spec hygiene: tolerate a server-side wart (two params collapsing to the
	// same CLI flag) rather than crashing — the client stays thin.
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
// first, so an operation declaring both `sortBy` and `sort_by` cannot panic.
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
