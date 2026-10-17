package host

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// actAsConnector makes this test binary, when the host runs it as itself,
// print the arguments it was given instead of running tests.
const actAsConnector = "CHEESE_HOST_TEST_ACT_AS_CONNECTOR"

func TestMain(m *testing.M) {
	if os.Getenv(actAsConnector) == "1" {
		fmt.Print(strings.Join(os.Args[1:], " "))
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// The server logs the owner's Claude Code out, or points it at a model
// service, by running this connector's own commands on the machine. The
// connector need not be on PATH, and the command must run under the config
// this connector runs with.
func TestTheServerRunsThisConnectorsOwnCommandsUnderItsConfig(t *testing.T) {
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	server := hostWithConfigOnAFakeServer(t, nil, cfgPath)
	server.send(t, link.Msg{
		T:       "exec",
		ID:      "logout",
		Command: []string{"cheesehost", "claude", "logout"},
		Env:     map[string]string{actAsConnector: "1"},
	})

	result := awaitExecResult(t, server, "logout")

	if result.Exit != 0 {
		t.Fatalf("the connector's own command did not run: %+v", result)
	}
	if want := "claude logout --config " + cfgPath; result.Stdout != want {
		t.Fatalf("ran with %q, want %q", result.Stdout, want)
	}
}
