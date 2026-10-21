package terminal

import "testing"

func TestDisconnectingOneEnrollmentKeepsTheOthersScreen(t *testing.T) {
	isolate(t)
	t.Setenv("CHEESE_PROFILE_ROOT", ".cheese")
	first, err := NewManager()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(first.KillServer)
	if _, err := first.Spawn("same-room", []string{"sleep", "60"}, nil, 80, 24); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CHEESE_PROFILE_ROOT", ".cheese/profiles/research")
	second, err := NewManager()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(second.KillServer)
	if second.HasSession("same-room") {
		t.Fatal("another enrollment adopted the first enrollment's screen")
	}
	if _, err := second.Spawn("same-room", []string{"sleep", "60"}, nil, 80, 24); err != nil {
		t.Fatal(err)
	}
	first.KillServer()
	if !second.HasSession("same-room") {
		t.Fatal("disconnecting the first enrollment killed the second")
	}
	// Restarting this enrollment must find its original screen.
	restarted, err := NewManager()
	if err != nil {
		t.Fatal(err)
	}
	if !restarted.HasSession("same-room") {
		t.Fatal("restart lost this enrollment's screen")
	}
}
