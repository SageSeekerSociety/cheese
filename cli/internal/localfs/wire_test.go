// The operation layer: decide first, touch the disk after, and never the other way
// round. These are the tests that would catch a reordering of Execute, which is the
// one change that would turn every check in this package into decoration.
package localfs

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestExecuteReadsAFileInsideTheGrant(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})

	reply := Execute(set, Op{Kind: OpRead, Path: filepath.Join(granted, "notes.txt")})
	if reply.Decision != DecisionAllowed {
		t.Fatalf("denied: %s (%s)", reply.Reason, reply.Detail)
	}
	if string(reply.Data) != "hello" {
		t.Errorf("data = %q, want hello", reply.Data)
	}
	if reply.Error != "" {
		t.Errorf("unexpected error: %s", reply.Error)
	}
}

func TestExecuteWritesAFileInsideTheGrant(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	target := filepath.Join(granted, "out.txt")

	reply := Execute(set, Op{Kind: OpWrite, Path: target, Data: []byte("written")})
	if reply.Decision != DecisionAllowed {
		t.Fatalf("denied: %s (%s)", reply.Reason, reply.Detail)
	}
	if reply.Error != "" {
		t.Fatalf("error: %s", reply.Error)
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if string(data) != "written" {
		t.Errorf("read back %q", data)
	}
}

func TestADeniedWriteNeverReachesTheDisk(t *testing.T) {
	// The whole feature in one assertion: a refused path must leave no trace. If
	// this fails, something opened a file before the decision came back and every
	// other check in this package is decoration.
	root, granted, outside := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	target := filepath.Join(outside, "should-not-exist.txt")

	reply := Execute(set, Op{Kind: OpWrite, Path: target, Data: []byte("nope")})
	if reply.Decision != DecisionDenied {
		t.Fatal("a write outside every grant must be denied")
	}
	// The refusal has to be readable, not a silent no.
	if reply.Reason != "no_grant" || reply.Detail == "" {
		t.Errorf("denial = %q / %q, want no_grant with a detail", reply.Reason, reply.Detail)
	}
	if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("%s exists after a denied write (%v)", target, err)
	}
	// And the parent directory is untouched too.
	entries, err := os.ReadDir(outside)
	if err != nil {
		t.Fatalf("readdir: %v", err)
	}
	if len(entries) != 1 || entries[0].Name() != "secret.txt" {
		t.Errorf("a denied write left something behind in %s: %v", root, entries)
	}
}

func TestADeniedReadReturnsNoContent(t *testing.T) {
	_, granted, outside := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	reply := Execute(set, Op{Kind: OpRead, Path: filepath.Join(outside, "secret.txt")})
	if reply.Decision != DecisionDenied {
		t.Fatal("a read outside every grant must be denied")
	}
	if len(reply.Data) != 0 {
		t.Errorf("a denied read returned %d bytes", len(reply.Data))
	}
}

func TestAWriteUnderAReadGrantIsRefusedWithAnActionableReason(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	target := filepath.Join(granted, "cannot-write.txt")
	reply := Execute(set, Op{Kind: OpWrite, Path: target, Data: []byte("x")})
	if reply.Decision != DecisionDenied {
		t.Fatal("a read grant must not authorize a write")
	}
	if reply.Reason != "read_only_grant" {
		t.Errorf("reason = %q, want read_only_grant", reply.Reason)
	}
	if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
		t.Error("the refused write reached the disk")
	}
}

func TestASymlinkEscapeIsRefusedThroughExecute(t *testing.T) {
	_, granted, outside := grantedDir(t)
	link := filepath.Join(granted, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable here: %v", err)
	}
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})

	read := Execute(set, Op{Kind: OpRead, Path: filepath.Join(link, "secret.txt")})
	if read.Decision != DecisionDenied || len(read.Data) != 0 {
		t.Errorf("a read through an escaping link was not refused: %+v", read)
	}
	write := Execute(set, Op{Kind: OpWrite, Path: filepath.Join(link, "planted.txt"), Data: []byte("x")})
	if write.Decision != DecisionDenied {
		t.Error("a write through an escaping link must be refused")
	}
	if _, err := os.Stat(filepath.Join(outside, "planted.txt")); !errors.Is(err, os.ErrNotExist) {
		t.Error("a write through an escaping link reached the disk")
	}
}

func TestExecuteListsAndReportsTheCap(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	reply := Execute(set, Op{Kind: OpList, Path: granted, Depth: 1})
	if reply.Decision != DecisionAllowed {
		t.Fatalf("denied: %s", reply.Reason)
	}
	if reply.Error != "" {
		t.Fatalf("error: %s", reply.Error)
	}
	if len(reply.Entries) == 0 {
		t.Error("a listing of the granted directory should not be empty")
	}
	if reply.Truncated {
		t.Error("a two-entry directory is not truncated")
	}
}

func TestAnUnknownOpIsRefusedRatherThanIgnored(t *testing.T) {
	// A silently-ignored write leaves a caller believing it saved.
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	reply := Execute(set, Op{Kind: OpKind("chmod"), Path: filepath.Join(granted, "notes.txt")})
	if reply.Error == "" {
		t.Fatal("an unknown op must be reported, not ignored")
	}
}

func TestExecuteReportsAReadFailureAsAnErrorNotADenial(t *testing.T) {
	// Authorized and then unreadable is not the same as refused, and writing
	// 「拒绝」 into the audit for it would be a lie about what happened.
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	reply := Execute(set, Op{Kind: OpRead, Path: filepath.Join(granted, "missing.txt")})
	if reply.Decision != DecisionAllowed {
		t.Fatalf("authorization should have been allowed, got %s", reply.Reason)
	}
	if reply.Error == "" {
		t.Error("a read that could not happen must report an error")
	}
	if len(reply.Data) != 0 {
		t.Error("a failed read returned content")
	}
}
