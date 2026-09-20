// The same table as backend/tests/unit/test_local_fs_paths.py, case for case.
//
// These are written against what a filesystem *does*, not against this package's
// internals: each case names a path a person could type (or an attacker could
// send) and the answer the disk would give. The two implementations are held to
// this one table on purpose — if they ever disagree, one of them is letting
// through what the other refuses, and that is the whole failure this pair exists
// to make impossible. Adding a case here means adding it there.
package localfs

import (
	"strings"
	"testing"
)

const bs = `\`

func TestNormalizeCollapsesToOneCanonicalForm(t *testing.T) {
	cases := []struct {
		raw      string
		platform Platform
		want     string
	}{
		// One path, one spelling — whichever separator the caller used.
		{"C:/Users/alice/MyDocs", PlatformWindows, "C:/Users/alice/MyDocs"},
		{"C:" + bs + "Users" + bs + "alice" + bs + "MyDocs", PlatformWindows,
			"C:/Users/alice/MyDocs"},
		// The canonical text keeps the caller's case except for the drive letter;
		// it is the comparison KEY that folds (asserted below).
		{"c:/users/alice/mydocs", PlatformWindows, "C:/users/alice/mydocs"},
		{"/home/alice/docs", PlatformLinux, "/home/alice/docs"},
		{"/Users/alice/Docs", PlatformMacOS, "/Users/alice/Docs"},
		// Dots collapse; a trailing slash is not a different directory.
		{"/home/alice/docs/./x/../y", PlatformLinux, "/home/alice/docs/y"},
		{"/home/alice/docs/", PlatformLinux, "/home/alice/docs"},
		// Win32 strips a trailing dot or blank from a segment before opening it, so
		// the normalized path has to strip it too or the two disagree.
		{"C:/Users/alice/MyDocs./x.txt", PlatformWindows,
			"C:/Users/alice/MyDocs/x.txt"},
		// A UNC share is a root, and the root is kept.
		{"//server/share/dir/a.txt", PlatformWindows,
			"//server/share/dir/a.txt"},
	}
	for _, c := range cases {
		got, err := Normalize(c.raw, c.platform)
		if err != nil {
			t.Errorf("Normalize(%q, %s) refused: %v", c.raw, c.platform, err)
			continue
		}
		if got.Text != c.want {
			t.Errorf("Normalize(%q, %s) = %q, want %q",
				c.raw, c.platform, got.Text, c.want)
		}
	}
}

func TestRefusedPathsAreRefusedByName(t *testing.T) {
	cases := []struct {
		raw      string
		platform Platform
		reason   string
	}{
		// A relative path names whatever directory the process happens to be in.
		// It cannot be authorized, because the directory it denotes is not fixed.
		{"Documents", PlatformLinux, "not_absolute"},
		{"./docs", PlatformLinux, "not_absolute"},
		{"../docs", PlatformLinux, "not_absolute"},
		{"~/Documents", PlatformLinux, "not_absolute"},
		{"Users/alice", PlatformWindows, "not_absolute"},
		{"/Users/alice", PlatformWindows, "not_absolute"},
		// C:foo means "foo under the current directory of drive C".
		{"C:foo", PlatformWindows, "drive_relative"},
		// An empty or control-character path is not a path.
		{"", PlatformLinux, "empty"},
		{"   ", PlatformLinux, "empty"},
		{"/home/alice/a\x00b", PlatformLinux, "control_char"},
		// Climbing above the root is an error, not a clamp. Clamping is what would
		// turn this into a silent success on /etc.
		{"/granted/../../etc", PlatformLinux, "escapes_root"},
		{"C:/granted/../../Windows", PlatformWindows, "escapes_root"},
		// The Win32 device namespaces reach the filesystem through a parser that
		// does not resolve dots and does not strip them. Refuse rather than guess.
		{"//?/C:/Users/alice", PlatformWindows, "device_prefix"},
		{"//./C:/Users/alice", PlatformWindows, "device_prefix"},
		// A reserved device name is not a file under the directory.
		{"C:/granted/NUL.txt", PlatformWindows, "reserved_name"},
		{"C:/granted/con", PlatformWindows, "reserved_name"},
		{"C:/granted/COM1", PlatformWindows, "reserved_name"},
		// A UNC path with no share is not a share root.
		{"//server", PlatformWindows, "unc_incomplete"},
	}
	for _, c := range cases {
		_, err := Normalize(c.raw, c.platform)
		refusal, ok := AsRefusal(err)
		if !ok {
			t.Errorf("Normalize(%q, %s) = %v, want a refusal with reason %q",
				c.raw, c.platform, err, c.reason)
			continue
		}
		if refusal.Reason != c.reason {
			t.Errorf("Normalize(%q, %s) refused as %q, want %q",
				c.raw, c.platform, refusal.Reason, c.reason)
		}
	}
}

func TestUnicodeDotIsNotADot(t *testing.T) {
	// Only the ASCII dot and dot-dot are special. A fullwidth dot is an ordinary
	// filename character, and treating it as a dot would let a segment be rewritten
	// into something the filesystem never resolves that way.
	got, err := Normalize("/home/alice/\u3002\u3002/secret", PlatformLinux)
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	want := []string{"home", "alice", "\u3002\u3002", "secret"}
	if strings.Join(got.Segments, "|") != strings.Join(want, "|") {
		t.Errorf("segments = %q, want %q", got.Segments, want)
	}
}

func TestContainmentIsBySegment(t *testing.T) {
	cases := []struct {
		grant, candidate string
		want             bool
	}{
		// The directory itself is inside itself.
		{"/home/alice/MyDocs", "/home/alice/MyDocs", true},
		{"/home/alice/MyDocs", "/home/alice/MyDocs/a.txt", true},
		{"/home/alice/MyDocs", "/home/alice/MyDocs/sub/deep/b.txt", true},
		// The bug this whole package exists for: a prefix is not containment.
		{"/home/alice/MyDocs", "/home/alice/MyDocuments/secret.txt", false},
		{"/home/alice/docs", "/home/alice/docs2", false},
		// A sibling, an ancestor, and somewhere else entirely.
		{"/home/alice/MyDocs", "/home/alice/Other", false},
		{"/home/alice/MyDocs", "/home/alice", false},
		{"/home/alice/MyDocs", "/etc/passwd", false},
		// A different root is a different filesystem.
		{"/home/alice", "//server/share/alice", false},
	}
	for _, c := range cases {
		grant, err := Normalize(c.grant, PlatformLinux)
		if err != nil {
			t.Fatalf("grant %q refused: %v", c.grant, err)
		}
		candidate, err := Normalize(c.candidate, PlatformLinux)
		if err != nil {
			t.Fatalf("candidate %q refused: %v", c.candidate, err)
		}
		if got := Contains(grant, candidate); got != c.want {
			t.Errorf("Contains(%q, %q) = %v, want %v",
				c.grant, c.candidate, got, c.want)
		}
	}
}

func TestCaseFoldingFollowsTheFilesystemNotTheString(t *testing.T) {
	// Two spellings are one grant on Windows and macOS, two on Linux.
	winGrant, _ := Normalize("C:/Users/Alice/MyDocs", PlatformWindows)
	winPath, _ := Normalize("c:/users/alice/mydocs/x", PlatformWindows)
	if !Contains(winGrant, winPath) {
		t.Error("Windows: C:/Users/Alice/MyDocs should contain c:/users/alice/mydocs/x")
	}

	macGrant, _ := Normalize("/Users/Alice/Docs", PlatformMacOS)
	macSame, _ := Normalize("/Users/Alice/Docs/x", PlatformMacOS)
	macOther, _ := Normalize("/users/alice/docs/x", PlatformMacOS)
	if !Contains(macGrant, macSame) || !Contains(macGrant, macOther) {
		t.Error("macOS: /Users/Alice/Docs should contain both spellings")
	}

	linuxGrant, _ := Normalize("/home/Alice/docs", PlatformLinux)
	linuxOther, _ := Normalize("/home/alice/docs/x", PlatformLinux)
	if Contains(linuxGrant, linuxOther) {
		t.Error("Linux: /home/Alice/docs must NOT contain /home/alice/docs/x")
	}
}

func TestEscapingAndNormalizingReachTheSameAnswer(t *testing.T) {
	// A path that climbs out and comes back is the real directory, and is judged as
	// that directory — the escape does not need to be refused to be safe, it just
	// must not be believed.
	grant, _ := Normalize("/home/alice/MyDocs", PlatformLinux)

	sneaky, err := Normalize("/home/alice/MyDocs/sub/../../Other/secret", PlatformLinux)
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	if sneaky.Text != "/home/alice/Other/secret" {
		t.Errorf("text = %q, want /home/alice/Other/secret", sneaky.Text)
	}
	if Contains(grant, sneaky) {
		t.Error("a path that climbed out must not be judged inside")
	}

	inside, err := Normalize("/home/alice/MyDocs/sub/../notes.txt", PlatformLinux)
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	if inside.Text != "/home/alice/MyDocs/notes.txt" {
		t.Errorf("text = %q, want /home/alice/MyDocs/notes.txt", inside.Text)
	}
	if !Contains(grant, inside) {
		t.Error("a path that climbed out and came back is inside")
	}
}

// TestFoldingIsFullCaseFolding pins the one place where a natural Go
// implementation would silently disagree with paths.py. strings.ToLower is the
// simple mapping: it leaves ß alone, so on a case-insensitive filesystem
// "/data/straße" and "/data/strasse" would be two different grants here and one
// there — and a grant made through one spelling would stop covering the other.
// Python's casefold, and this package's fold, expand ß to ss.
func TestFoldingIsFullCaseFolding(t *testing.T) {
	if got := fold.String("straße"); got != "strasse" {
		t.Fatalf("fold(straße) = %q, want strasse — full case folding is not in use", got)
	}
	if got := fold.String("\uFB00"); got != "ff" {
		t.Fatalf("fold(ﬀ) = %q, want ff", got)
	}

	grant, err := Normalize("/data/straße", PlatformLinux)
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	// Linux is case-sensitive, so these are genuinely two directories there.
	other, _ := Normalize("/data/strasse/x", PlatformLinux)
	if Contains(grant, other) {
		t.Error("Linux: ß and ss are different names")
	}

	// On macOS they are one name, and the grant has to cover both spellings.
	macGrant, _ := Normalize("/Data/straße", PlatformMacOS)
	macOther, _ := Normalize("/data/strasse/x", PlatformMacOS)
	if !Contains(macGrant, macOther) {
		t.Error("macOS: a grant on straße must cover strasse")
	}
}

func TestUnknownPlatformIsRefused(t *testing.T) {
	// A platform this package cannot normalize for must not silently borrow
	// another one's rules — guessing wrong here is a wrong case-folding decision,
	// which is a wrong containment decision.
	if _, err := Normalize("/home/alice/docs", Platform("plan9")); err == nil {
		t.Fatal("an unknown platform must be refused")
	}
}

func TestADirectoryIsAlwaysItsOwnGrant(t *testing.T) {
	grant, _ := Normalize("/home/alice/MyDocs", PlatformLinux)
	if !Contains(grant, grant) {
		t.Error("a directory must contain itself, or an exact-path access would be denied")
	}
	if grant.IsRoot() {
		t.Error("a three-segment path is not a root")
	}
	root, _ := Normalize("/", PlatformLinux)
	if !root.IsRoot() {
		t.Error("/ is a root and must be refused by Grant")
	}
	drive, _ := Normalize("C:/", PlatformWindows)
	if !drive.IsRoot() {
		t.Error("C:/ is a root and must be refused by Grant")
	}
}
