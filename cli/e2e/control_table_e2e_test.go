//go:build claudee2e

// The metering proxy's answer table, checked against a REAL Claude Code boot.
//
// A machine has one launch shape now (结论 46): no base URL, the proxy on
// HTTPS_PROXY, a fake ticket. Nothing in that shape reaches Anthropic except
// the model call itself — every other request Claude Code makes on its way up
// is answered by deploy/metering-proxy from a table, so a deployment that owns
// no subscription can still get a screen to a prompt, and so a sandbox never
// receives an echo of the platform account's uuid, email and organization.
//
// A table is only as good as the list of paths it was written against, and that
// list can only come from the client. So this file reads the SAME rows the
// proxy serves (deploy/metering-proxy/control_answers.json), scripts them onto
// the stand-in Anthropic API, boots a real Claude Code against them, and then
// reports what the boot actually asked for. Two things fail the test:
//
//   - a non-model path this table does not answer — in production that request
//     leaves the box on the platform's credential;
//   - a non-model request that came back anything but 2xx.
//
// What it does NOT prove: the shape here still sets ANTHROPIC_BASE_URL, which
// is the one thing the production launch environment never does, so paths that
// only an OAuth-mode client asks for (and the Statsig hosts, which never reach
// this mock) are outside what a MockServer stand-in can observe. Those rows are
// asserted by the unit table instead.
package e2e

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The verified place a real token would carry. Only the shape matters here.
const (
	tableProject = "11111111-1111-1111-1111-111111111111"
	tableTopic   = "22222222-2222-2222-2222-222222222222"
)

type controlRow struct {
	Exact  string          `json:"exact"`
	Prefix string          `json:"prefix"`
	Hosts  []string        `json:"hosts"`
	Status int             `json:"status"`
	Body   json.RawMessage `json:"body"`
}

type controlTable struct {
	RewriteLimit int          `json:"model_rewrite_limit_bytes"`
	Rows         []controlRow `json:"rows"`
}

// bootHost is the one name a sandbox's launch shape talks to. The proxy also
// MITMs console.anthropic.com and platform.claude.com, where a human's `claude
// /login` asks for these same paths against a real Anthropic account — so a row
// only counts here if it serves this host, and a row naming no host serves it by
// default. This mock stands in for that host and nothing else.
const bootHost = "api.anthropic.com"

func (r controlRow) servesBootHost() bool {
	if len(r.Hosts) == 0 {
		return true
	}
	for _, host := range r.Hosts {
		if host == bootHost {
			return true
		}
	}
	return false
}

// covers answers the question the test exists to ask: would the proxy have
// answered this path itself, or would it have gone upstream?
func (c controlTable) covers(path string) bool {
	path, _, _ = strings.Cut(path, "?")
	for _, row := range c.Rows {
		if !row.servesBootHost() {
			continue
		}
		if row.Exact != "" && path == row.Exact {
			return true
		}
		if row.Prefix != "" && strings.HasPrefix(path, row.Prefix) {
			return true
		}
	}
	return false
}

func loadControlTable(t *testing.T) controlTable {
	t.Helper()
	path := filepath.Join("..", "..", "deploy", "metering-proxy", "control_answers.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read the proxy's answer table (%s): %v", path, err)
	}
	var table controlTable
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	if len(table.Rows) == 0 || table.RewriteLimit == 0 {
		t.Fatalf("%s carries no rows or no rewrite limit", path)
	}
	return table
}

// installControlTable scripts the mock to answer exactly what the proxy would.
//
// Rows for another host are skipped — the Statsig names, and anything a later
// row addresses to the login hosts: this client is pointed at the mock by
// ANTHROPIC_BASE_URL, so nothing addressed elsewhere arrives here at all.
func installControlTable(t *testing.T, base string, table controlTable) {
	t.Helper()
	for _, row := range table.Rows {
		if !row.servesBootHost() {
			continue
		}
		match := row.Exact
		if match == "" {
			if row.Prefix == "" {
				continue
			}
			match = row.Prefix + ".*"
		}
		response := fmt.Sprintf(`{"statusCode": %d}`, row.Status)
		if len(row.Body) > 0 && string(row.Body) != "null" {
			body := string(row.Body)
			body = strings.ReplaceAll(body, `"{project}"`, `"`+tableProject+`"`)
			body = strings.ReplaceAll(body, `"{topic}"`, `"`+tableTopic+`"`)
			encoded, err := json.Marshal(body)
			if err != nil {
				t.Fatalf("encode the table body for %s: %v", match, err)
			}
			response = fmt.Sprintf(
				`{"statusCode": %d, "headers": {"content-type": ["application/json"]}, "body": %s}`,
				row.Status, encoded)
		}
		mockserverPut(t, base, "/mockserver/expectation", fmt.Sprintf(
			`{ "httpRequest": { "path": %q },
			   "priority": 5,
			   "httpResponse": %s }`, match, response))
	}
}

// TestTheAnswerTableCoversWhatARealBootAsksFor is the acceptance run for the one
// launch environment: a real Claude Code, booting under the proxy's own
// answers, completing a real Bash tool call, with every non-model request it
// made accounted for by the table and answered 2xx.
func TestTheAnswerTableCoversWhatARealBootAsksFor(t *testing.T) {
	table := loadControlTable(t)
	// A real turn, not just a boot: the tool call is what proves the process
	// came all the way up under these answers. The mark file is written by the
	// scripted Bash call.
	f := startHeadlessClaude(t, "【平台】请运行那个工具。")
	waitFor(t, "the tool call to land", 150*time.Second,
		func() bool { return f.marks() >= 1 },
		func() { t.Logf("--- claude stderr ---\n%s", f.stderr.String()) })

	exchanges, err := f.recordedExchanges()
	if err != nil {
		t.Fatalf("read what the mock recorded: %v", err)
	}
	var observed, uncovered, notOK []string
	for _, e := range exchanges {
		path := e.path
		if strings.HasPrefix(path, "/mockserver") || strings.Contains(path, "/v1/messages") {
			continue
		}
		observed = append(observed, fmt.Sprintf("%s %s -> %d", e.method, path, e.status))
		if !table.covers(path) {
			uncovered = append(uncovered, path)
		}
		if e.status < 200 || e.status >= 300 {
			notOK = append(notOK, fmt.Sprintf("%s -> %d", path, e.status))
		}
	}
	// The capture itself, in the log: this is the list the table is written
	// against, and a client version that changes it should be readable here
	// rather than inferred from a failure.
	t.Logf("non-model requests this boot made (%d):\n  %s",
		len(observed), strings.Join(observed, "\n  "))

	if len(uncovered) > 0 {
		t.Fatalf("the proxy's answer table does not answer %v — in production "+
			"each of these leaves the box for Anthropic on the platform's "+
			"credential; add the row to deploy/metering-proxy/control_answers.json",
			uncovered)
	}
	if len(notOK) > 0 {
		t.Fatalf("non-model requests that did not come back 2xx: %v", notOK)
	}

	assertModelArrivesInsideTheProxysHead(t, f, table)
}

// assertModelArrivesInsideTheProxysHead is the other half of the control point.
//
// The launch environment names no model, so the binding resolved at admission
// is written into the request body as it streams past — and the proxy may only
// hold the head of that body while it looks for the top-level `model` member
// (holding a whole grown conversation is the #654 OOM). The budget is therefore
// a bet on where this client puts that member, and this is the only place that
// bet is checked against a real request instead of against a reading of the
// code. A body that named its model too late is refused, not quietly run on
// whatever the CLI picked, so the cost of being wrong here is a dead turn.
func assertModelArrivesInsideTheProxysHead(t *testing.T, f *bootFixture, table controlTable) {
	t.Helper()
	body, err := f.lastConversationBody()
	if err != nil {
		t.Fatalf("read the recorded conversation body: %v", err)
	}
	offset, order, err := topLevelMemberOffset(body, "model")
	if err != nil {
		t.Fatalf("scan the recorded conversation body: %v", err)
	}
	if offset < 0 {
		t.Fatalf("a real /v1/messages body carried no top-level model member; "+
			"top-level members in order: %v", order)
	}
	t.Logf("real body: %d bytes, top-level model member at byte %d (budget %d); "+
		"members in order: %v", len(body), offset, table.RewriteLimit, order)
	if offset >= table.RewriteLimit {
		t.Fatalf("this client serializes its top-level model member at byte %d, "+
			"past the proxy's %d-byte head budget — every turn of a long "+
			"conversation would be refused; raise model_rewrite_limit_bytes in "+
			"deploy/metering-proxy/control_answers.json against this number",
			offset, table.RewriteLimit)
	}
}

// topLevelMemberOffset reports where a top-level member's KEY starts, and the
// order of the top-level members, reading the document as a token stream so the
// order survives (a Go map would lose it).
func topLevelMemberOffset(body []byte, want string) (int, []string, error) {
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	token, err := decoder.Token()
	if err != nil {
		return 0, nil, err
	}
	if delimiter, ok := token.(json.Delim); !ok || delimiter != '{' {
		return 0, nil, fmt.Errorf("body is not a JSON object: starts with %v", token)
	}
	offset, order := -1, []string{}
	for decoder.More() {
		start := decoder.InputOffset()
		key, err := decoder.Token()
		if err != nil {
			return 0, order, err
		}
		name, ok := key.(string)
		if !ok {
			return 0, order, fmt.Errorf("a top-level member name was not a string: %v", key)
		}
		order = append(order, name)
		if name == want && offset < 0 {
			// Where the member begins, to within the comma before it — the
			// budget is tens of KB, so a byte or two is not the question.
			offset = int(start)
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return 0, order, err
		}
	}
	return offset, order, nil
}
