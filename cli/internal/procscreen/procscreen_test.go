package procscreen

import (
	"errors"
	"testing"
)

// fakeSystem stands in for the operating system: programs it started, which
// of them still run, and when the machine booted.
type fakeSystem struct {
	next    int
	alive   map[int]bool
	ended   []int
	boot    int64
	clock   int64
	failing bool
}

func (f *fakeSystem) start(argv, env []string, log string) (int, int64, error) {
	if f.failing {
		return 0, 0, errors.New("cannot start")
	}
	f.next++
	f.clock++
	f.alive[f.next] = true
	return f.next, f.clock, nil
}

func (f *fakeSystem) running(pid int, _ int64) bool { return f.alive[pid] }

func (f *fakeSystem) end(pid int) error {
	f.ended = append(f.ended, pid)
	f.alive[pid] = false
	return nil
}

func (f *fakeSystem) booted() int64 { return f.boot }

func newFake(t *testing.T) (*Manager, *fakeSystem) {
	t.Helper()
	sys := &fakeSystem{alive: map[int]bool{}}
	m, err := newManager(t.TempDir(), sys)
	if err != nil {
		t.Fatal(err)
	}
	return m, sys
}

func TestAScreenThatExitedIsAdoptedNotStartedAgain(t *testing.T) {
	m, sys := newFake(t)
	if _, err := m.Spawn("s1", []string{"bash"}, nil, 0, 0); err != nil {
		t.Fatal(err)
	}
	sys.alive[1] = false // the program finished; nobody has closed the screen

	if !m.HasSession("s1") {
		t.Fatal("a screen whose program exited is gone before it was closed")
	}
}

func TestAScreenFromBeforeARebootIsGone(t *testing.T) {
	m, sys := newFake(t)
	if _, err := m.Spawn("s1", []string{"bash"}, nil, 0, 0); err != nil {
		t.Fatal(err)
	}
	if err := m.SaveIdentity("s1", "https://cheese.example", `{"sid":"s1"}`); err != nil {
		t.Fatal(err)
	}
	sys.alive[1] = false
	sys.boot = 100 // the machine rebooted after the program started

	if m.HasSession("s1") {
		t.Fatal("a screen survived a reboot")
	}
	ids, err := m.Identities("https://cheese.example")
	if err != nil || len(ids) != 0 {
		t.Fatalf("identities after a reboot: %v, %v", ids, err)
	}
}

func TestOnlyTheScreensOneServerLaunchedAreItsToRestore(t *testing.T) {
	m, _ := newFake(t)
	for name, owner := range map[string]string{"mine": "https://a.example", "theirs": "https://b.example"} {
		if _, err := m.Spawn(name, []string{"bash"}, nil, 0, 0); err != nil {
			t.Fatal(err)
		}
		if err := m.SaveIdentity(name, owner, `{"sid":"`+name+`"}`); err != nil {
			t.Fatal(err)
		}
	}
	ids, err := m.Identities("https://a.example")
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 || ids["mine"] != `{"sid":"mine"}` {
		t.Fatalf("identities: %v", ids)
	}
}

func TestClosingAScreenEndsItsProgramAndForgetsIt(t *testing.T) {
	m, sys := newFake(t)
	s, err := m.Spawn("s1", []string{"bash"}, nil, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	if len(sys.ended) != 1 || sys.ended[0] != 1 {
		t.Fatalf("ended: %v", sys.ended)
	}
	if m.HasSession("s1") {
		t.Fatal("a closed screen is still there")
	}
}

func TestAProgramThatCannotStartLeavesNoScreen(t *testing.T) {
	m, sys := newFake(t)
	sys.failing = true
	if _, err := m.Spawn("s1", []string{"bash"}, nil, 0, 0); err == nil {
		t.Fatal("spawn reported success")
	}
	if m.HasSession("s1") {
		t.Fatal("a screen that never started exists")
	}
}

func TestAScreenHasNoTerminalToShow(t *testing.T) {
	m, _ := newFake(t)
	s, err := m.Spawn("s1", []string{"bash"}, nil, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Attach(80, 24, func([]byte) {}); !errors.Is(err, ErrNoView) {
		t.Fatalf("attach: %v", err)
	}
}
