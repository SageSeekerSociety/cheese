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
