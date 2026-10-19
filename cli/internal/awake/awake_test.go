package awake

import "testing"

func TestRunningSessionsHoldOneAssertion(t *testing.T) {
	acquired, released := 0, 0
	g := New(func() (func(), error) { acquired++; return func() { released++ }, nil })
	for _, n := range []int{0, 1, 2, 1} {
		if err := g.Update(true, n); err != nil {
			t.Fatal(err)
		}
	}
	if acquired != 1 || released != 0 {
		t.Fatalf("while one session remains: acquired=%d released=%d", acquired, released)
	}
	_ = g.Update(true, 0)
	if released != 1 {
		t.Fatalf("last session ended: released=%d", released)
	}
	_ = g.Update(false, 3)
	if acquired != 1 {
		t.Fatalf("disabled: acquired=%d", acquired)
	}
	_ = g.Update(true, 1)
	_ = g.Update(false, 1)
	if acquired != 2 || released != 2 {
		t.Fatalf("disabled during work: acquired=%d released=%d", acquired, released)
	}
	g.Close()
	if released != 2 {
		t.Fatalf("double release: %d", released)
	}
}
