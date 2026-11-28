package host

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
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

// The server's programs run on the Python this connector placed, by path,
// whatever `python3` PATH finds first: on a person's machine that one is
// theirs, kept in a home the session may not see.
func TestTheServersPythonIsThePlacedOneNotPaths(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stand-in Pythons are shell scripts")
	}
	bin := t.TempDir()
	owners := filepath.Join(bin, "python3")
	if err := os.WriteFile(owners, []byte("#!/bin/sh\necho the owner's python\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	placed := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(placed, []byte("#!/bin/sh\necho placed \"$@\"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("CHEESE_PYTHON", placed)
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "exec", ID: "python", Command: []string{"python3", "-"}})

	result := awaitExecResult(t, server, "python")

	if result.Exit != 0 || strings.TrimSpace(result.Stdout) != "placed -" {
		t.Fatalf("ran on another Python: %+v", result)
	}
}

// With no Python placed the server's program does not run at all, on the
// owner's or any other, and the result says why.
func TestNoPlacedPythonRunsNothingAndSaysSo(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stand-in Pythons are shell scripts")
	}
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "python3"), []byte("#!/bin/sh\necho the owner's python\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("CHEESE_PYTHON", "")
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "exec", ID: "python", Command: []string{"python3", "-"}})

	result := awaitExecResult(t, server, "python")

	if result.Exit == 0 || result.Stdout != "" || !strings.Contains(result.Stderr, "no Python of the platform's placed") {
		t.Fatalf("ran without the placed Python: %+v", result)
	}
}
