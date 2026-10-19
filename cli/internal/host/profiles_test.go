package host

import (
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

func TestExecutionUsesTheEnrollmentRootRatherThanAServerOverride(t *testing.T) {
	t.Setenv("CHEESE_PROFILE_ROOT", ".cheese/profiles/research")
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "exec", ID: "root", Command: []string{"sh", "-c", `printf %s "$CHEESE_PROFILE_ROOT"`}, Env: map[string]string{"CHEESE_PROFILE_ROOT": ".cheese"}})
	result := awaitExecResult(t, server, "root")
	if result.Exit != 0 || result.Stdout != ".cheese/profiles/research" {
		t.Fatalf("execution escaped its enrollment: %+v", result)
	}
}

// A path the backend names in a command's environment is a path in this
// enrollment's workspace: the command reads it as such, not as the placeholder.
func TestAnExecsEnvironmentNamesThisEnrollmentsWorkspace(t *testing.T) {
	t.Setenv("CHEESE_PROFILE_ROOT", ".cheese/profiles/research")
	server := hostOnAFakeServer(t, nil)
	server.send(t, link.Msg{T: "exec", ID: "env", Command: []string{"sh", "-c", `printf %s "$SEAT"`}, Env: map[string]string{"SEAT": "$HOME/${CHEESE_PROFILE_ROOT:-.cheese}/home/p/r"}})
	result := awaitExecResult(t, server, "env")
	if result.Exit != 0 || result.Stdout != "$HOME/.cheese/profiles/research/home/p/r" {
		t.Fatalf("the command was handed another workspace: %+v", result)
	}
}
