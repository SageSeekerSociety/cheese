// The decision, the resolution and the operations, exercised against a real
// temporary directory. These are the device's own answers — the half that has the
// disk in front of it — and they are checked here so that a platform that is
// wrong cannot be the only thing standing between a request and somebody's files.
package localfs

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// grantedDir builds a temporary directory laid out as the owner's disk would be,
// and returns the paths the tests reason about.
func grantedDir(t *testing.T) (root, granted, outside string) {
	t.Helper()
	root = t.TempDir()
	granted = filepath.Join(root, "granted")
	outside = filepath.Join(root, "outside")
	for _, dir := range []string{granted, outside} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", dir, err)
		}
	}
	// A sibling whose name starts with the granted directory's name. This is the
	// prefix bug in its natural habitat.
	if err := os.MkdirAll(granted+"Xtra", 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(granted, "notes.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("secret"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	return root, granted, outside
}

func mustSet(t *testing.T, grants ...Grant) *GrantSet {
	t.Helper()
	set, err := NewGrantSet("device-1", grants)
	if err != nil {
		t.Fatalf("NewGrantSet: %v", err)
	}
	return set
}

func ask(path string, needed Mode, projectID string) Request {
	return Request{Path: path, Needed: needed, ProjectID: projectID}
}

func TestAGrantedDirectoryAndItsContentsAreAllowed(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})

	for _, path := range []string{
		granted,
		filepath.Join(granted, "notes.txt"),
		filepath.Join(granted, "deep", "not", "yet", "made.txt"),
	} {
		if got := Decide(set, ask(path, ModeRead, "")); !got.Allowed() {
			t.Errorf("Decide(%q) = %s (%s), want allowed", path, got.Decision, got.Reason)
		}
	}
}

func TestAPathOutsideEveryGrantIsDeniedByName(t *testing.T) {
	_, granted, outside := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})

	got := Decide(set, ask(filepath.Join(outside, "secret.txt"), ModeRead, ""))
	if got.Allowed() {
		t.Fatal("a path outside every grant must be denied")
	}
	if got.Reason != "no_grant" {
		t.Errorf("reason = %q, want no_grant", got.Reason)
	}
	// The refusal has to be something a person can read, not a silent no.
	if got.Detail == "" {
		t.Error("a denial must carry a detail the screen can show")
	}
}

func TestASiblingThatSharesAPrefixIsNotInside(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})

	// grantedXtra is not inside granted, and startswith says it is.
	sibling := granted + "Xtra"
	if got := Decide(set, ask(sibling, ModeRead, "")); got.Allowed() {
		t.Fatalf("%s must not be covered by a grant on %s", sibling, granted)
	}
}

func TestAReadGrantDoesNotAuthorizeAWrite(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	target := filepath.Join(granted, "notes.txt")

	if got := Decide(set, ask(target, ModeRead, "")); !got.Allowed() {
		t.Fatalf("reading a read-granted file = %s (%s)", got.Decision, got.Reason)
	}
	got := Decide(set, ask(target, ModeReadWrite, ""))
	if got.Allowed() {
		t.Fatal("a read grant must not authorize a write")
	}
	// Naming the narrower cause is what makes the refusal actionable — 「越界了」
	// would send the owner looking for a grant that is already there.
	if got.Reason != "read_only_grant" {
		t.Errorf("reason = %q, want read_only_grant", got.Reason)
	}
	if got.Grant == nil || got.Grant.ID != "g1" {
		t.Error("the refusal should name the grant that would have covered it")
	}
}

func TestAWriteGrantAlsoCoversReading(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	if got := Decide(set, ask(filepath.Join(granted, "notes.txt"), ModeRead, "")); !got.Allowed() {
		t.Fatalf("read-write must cover read, got %s (%s)", got.Decision, got.Reason)
	}
}

func TestAProjectGrantCoversOnlyThatProject(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeProject, ProjectID: "project-a",
	})
	file := filepath.Join(granted, "notes.txt")

	if got := Decide(set, ask(file, ModeRead, "project-a")); !got.Allowed() {
		t.Fatalf("the granted project must be covered, got %s (%s)", got.Decision, got.Reason)
	}
	for _, other := range []string{"project-b", ""} {
		if got := Decide(set, ask(file, ModeRead, other)); got.Allowed() {
			t.Errorf("a project grant must not cover project %q", other)
		}
	}
}

func TestAUserGrantCoversAllOfTheOwnersWork(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	file := filepath.Join(granted, "notes.txt")
	for _, project := range []string{"project-a", "project-b", ""} {
		if got := Decide(set, ask(file, ModeRead, project)); !got.Allowed() {
			t.Errorf("a user grant must cover every project, %q was denied (%s)",
				project, got.Reason)
		}
	}
}

func TestTheWholeDiskCannotBeGrantedEvenByThePlatform(t *testing.T) {
	// The platform refuses a root grant when the owner asks for one. This is the
	// same refusal on the device, for the case where the set arrived from a
	// platform that is buggy, rolled back, or lying.
	for _, root := range []string{"/", "//server/share"} {
		platform := PlatformLinux
		if root != "/" {
			platform = PlatformWindows
		}
		set := mustSet(t, Grant{
			ID: "g1", Path: root, Platform: platform, Mode: ModeReadWrite,
			Scope: ScopeUser,
		})
		if got := Decide(set, ask("/etc/passwd", ModeReadWrite, "")); got.Allowed() {
			t.Errorf("a grant on %q must not authorize anything", root)
		}
	}
}

func TestNoGrantSetIsDeniedByName(t *testing.T) {
	got := Decide(nil, ask("/home/alice/notes.txt", ModeRead, ""))
	if got.Allowed() {
		t.Fatal("having been told about no grants is not permission")
	}
	if got.Reason != "no_grant_set" {
		t.Errorf("reason = %q, want no_grant_set", got.Reason)
	}
}

func TestARevokedGrantIsGoneFromTheFingerprint(t *testing.T) {
	// A revoke empties the set, and the fingerprint of the emptied set differs
	// from the one that had the grant — which is how a revoke reaches a device
	// that was offline when it happened.
	withGrant := mustSet(t, Grant{
		ID: "g1", Path: "/home/alice/MyDocs", Platform: PlatformLinux,
		Mode: ModeRead, Scope: ScopeUser,
	})
	without := mustSet(t)
	if withGrant.Fingerprint == without.Fingerprint {
		t.Fatal("revoking a grant must change the fingerprint, or the device is never re-sent")
	}

	// Order is not a fact about the set, so it must not move the fingerprint.
	a := mustSet(t,
		Grant{ID: "g1", Path: "/home/alice/A", Platform: PlatformLinux, Mode: ModeRead, Scope: ScopeUser},
		Grant{ID: "g2", Path: "/home/alice/B", Platform: PlatformLinux, Mode: ModeRead, Scope: ScopeUser},
	)
	b := mustSet(t,
		Grant{ID: "g2", Path: "/home/alice/B", Platform: PlatformLinux, Mode: ModeRead, Scope: ScopeUser},
		Grant{ID: "g1", Path: "/home/alice/A", Platform: PlatformLinux, Mode: ModeRead, Scope: ScopeUser},
	)
	if a.Fingerprint != b.Fingerprint {
		t.Error("the fingerprint must not depend on the order the grants arrived in")
	}
}

// -- symlinks: the escape a lexical check cannot see ----------------------

func TestASymlinkOutOfTheGrantedDirectoryIsDenied(t *testing.T) {
	_, granted, outside := grantedDir(t)
	link := filepath.Join(granted, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable here: %v", err)
	}

	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	// Lexically this is inside the grant. On disk it is outside, and that is the
	// answer that counts.
	got := Decide(set, ask(filepath.Join(link, "secret.txt"), ModeRead, ""))
	if got.Allowed() {
		t.Fatal("a symlink out of the granted directory must not be followed")
	}

	// The link itself, read as a file, is also outside.
	if got := Decide(set, ask(link, ModeRead, "")); got.Allowed() {
		t.Fatal("the symlink itself resolves outside the grant")
	}
}

func TestASymlinkThatStaysInsideIsAllowed(t *testing.T) {
	_, granted, _ := grantedDir(t)
	real := filepath.Join(granted, "real")
	if err := os.MkdirAll(real, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(real, "in.txt"), []byte("in"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	link := filepath.Join(granted, "alias")
	if err := os.Symlink(real, link); err != nil {
		t.Skipf("symlinks unavailable here: %v", err)
	}

	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	if got := Decide(set, ask(filepath.Join(link, "in.txt"), ModeRead, "")); !got.Allowed() {
		t.Fatalf("a link that stays inside the grant is inside it, got %s (%s)",
			got.Decision, got.Reason)
	}
}

func TestASymlinkedGrantedDirectoryStillCoversItsContents(t *testing.T) {
	// The owner authorized a path that happens to be a link. Both the name they
	// typed and where it leads have to work, or the folder they authorized is
	// unusable through the name they authorized it by.
	root := t.TempDir()
	real := filepath.Join(root, "real")
	if err := os.MkdirAll(real, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(real, "f.txt"), []byte("x"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Skipf("symlinks unavailable here: %v", err)
	}

	set := mustSet(t, Grant{
		ID: "g1", Path: link, Platform: PlatformLinux, Mode: ModeRead,
		Scope: ScopeUser,
	})
	if got := Decide(set, ask(filepath.Join(link, "f.txt"), ModeRead, "")); !got.Allowed() {
		t.Fatalf("the authorized name must work, got %s (%s)", got.Decision, got.Reason)
	}

	// The other spelling is a path the owner did not name, and it is refused. A
	// grant is a name, not an inode: requiring both the lexical and the resolved
	// form to be inside is what keeps a link planted AT the granted name from
	// extending the grant to wherever it points — the owner authorizes a folder,
	// not whatever that folder is later swapped for.
	if got := Decide(set, ask(filepath.Join(real, "f.txt"), ModeRead, "")); got.Allowed() {
		t.Fatal("a path the owner did not name must not be covered by their grant")
	}
}

// -- resolution -----------------------------------------------------------

func TestResolvePathExpandsTheTilde(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Skipf("no home directory: %v", err)
	}
	got, err := ResolvePath("~/Documents")
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	if want := filepath.Join(home, "Documents"); got.Lexical.Text != want {
		t.Errorf("Lexical = %q, want %q", got.Lexical.Text, want)
	}
}

func TestResolvePathRefusesAnotherUsersHome(t *testing.T) {
	// Resolving somebody else's home means reading the password database or
	// inventing a convention; the person who typed it meant a specific directory
	// this code would be guessing at.
	_, err := ResolvePath("~someoneelse/docs")
	refusal, ok := AsRefusal(err)
	if !ok {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if refusal.Reason != "other_user_home" {
		t.Errorf("reason = %q, want other_user_home", refusal.Reason)
	}
}

func TestResolvePathRefusesARelativePath(t *testing.T) {
	_, err := ResolvePath("docs/notes.txt")
	refusal, ok := AsRefusal(err)
	if !ok {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if refusal.Reason != "not_absolute" {
		t.Errorf("reason = %q, want not_absolute", refusal.Reason)
	}
}

func TestResolvePathKeepsTheLeafOfAWriteThatDoesNotExistYet(t *testing.T) {
	root := t.TempDir()
	// The file does not exist; the directory does. The answer has to name where
	// the write would land, not refuse merely because nothing is there yet.
	got, err := ResolvePath(filepath.Join(root, "brand-new.txt"))
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	if !got.RealKnown {
		t.Error("an existing parent is enough to resolve; a new file must be writable")
	}
	if got.OnDisk != filepath.Join(root, "brand-new.txt") {
		t.Errorf("OnDisk = %q", got.OnDisk)
	}
}

// -- the operations -------------------------------------------------------

func TestReadWriteAndListInsideTheBounds(t *testing.T) {
	_, granted, _ := grantedDir(t)
	set := mustSet(t, Grant{
		ID: "g1", Path: granted, Platform: PlatformLinux, Mode: ModeReadWrite,
		Scope: ScopeUser,
	})
	target := filepath.Join(granted, "made.txt")

	verdict := Decide(set, ask(target, ModeReadWrite, ""))
	if !verdict.Allowed() {
		t.Fatalf("write was denied: %s", verdict.Reason)
	}
	resolved, err := ResolvePath(target)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	if err := WriteFile(resolved, []byte("written"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	verdict = Decide(set, ask(target, ModeRead, ""))
	if !verdict.Allowed() {
		t.Fatalf("read was denied: %s", verdict.Reason)
	}
	resolved, err = ResolvePath(target)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	data, err := ReadFile(resolved)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(data) != "written" {
		t.Errorf("read back %q, want %q", data, "written")
	}

	dirResolved, err := ResolvePath(granted)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	entries, err := ListDir(dirResolved, ListOptions{})
	if err != nil {
		t.Fatalf("ListDir: %v", err)
	}
	names := make(map[string]bool, len(entries))
	for _, e := range entries {
		names[e.Name] = true
	}
	if !names["notes.txt"] || !names["made.txt"] {
		t.Errorf("listing is missing files it should show: %v", names)
	}
}

func TestListingIsBoundedInDepth(t *testing.T) {
	root := t.TempDir()
	walk := root
	for i := 0; i < 8; i++ {
		walk = filepath.Join(walk, "level")
	}
	if err := os.MkdirAll(walk, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	resolved, err := ResolvePath(root)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	// Asking for more depth than the cap must be clamped, not honoured: this is
	// what keeps 大目录 from being walked.
	entries, err := ListDir(resolved, ListOptions{Depth: 999})
	if err != nil {
		t.Fatalf("ListDir: %v", err)
	}
	deepest := 0
	for _, e := range entries {
		if e.IsDir {
			deepest++
		}
	}
	if deepest > MaxListDepth {
		t.Errorf("a listing descended %d levels, cap is %d", deepest, MaxListDepth)
	}
}

func TestListingExcludesAndDoesNotDescendIntoSymlinks(t *testing.T) {
	root, granted, outside := grantedDir(t)
	if err := os.MkdirAll(filepath.Join(granted, "node_modules"), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(granted, "node_modules", "junk.js"), []byte("x"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	link := filepath.Join(granted, "elsewhere")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable here: %v", err)
	}

	resolved, err := ResolvePath(granted)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	entries, err := ListDir(resolved, ListOptions{Depth: 3, Exclude: []string{"node_modules"}})
	if err != nil {
		t.Fatalf("ListDir: %v", err)
	}
	for _, e := range entries {
		if e.Name == "node_modules" {
			t.Error("an excluded name must not appear")
		}
		if e.Name == "junk.js" {
			t.Error("an excluded directory must not be descended into")
		}
		if e.Name == "secret.txt" {
			t.Error("a listing must not follow a symlink out of the granted directory")
		}
	}
	found := false
	for _, e := range entries {
		if e.Name == "elsewhere" {
			found = true
			if !e.Symlink {
				t.Error("a link must be reported as a link")
			}
		}
	}
	if !found {
		t.Error("a link inside the directory should be reported, just not followed")
	}
	_ = root
}

func TestAReadRefusesAFileOverTheLimitRatherThanTruncating(t *testing.T) {
	// A truncated file that looks complete is worse than a refusal that names the
	// size.
	_, granted, _ := grantedDir(t)
	big := filepath.Join(granted, "big.bin")
	f, err := os.Create(big)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if err := f.Truncate(MaxReadBytes + 1); err != nil {
		f.Close()
		t.Fatalf("truncate: %v", err)
	}
	f.Close()

	resolved, err := ResolvePath(big)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	_, err = ReadFile(resolved)
	var tooLarge *ErrTooLarge
	if !errors.As(err, &tooLarge) {
		t.Fatalf("err = %v, want ErrTooLarge", err)
	}
	if tooLarge.Limit != MaxReadBytes {
		t.Errorf("limit = %d, want %d", tooLarge.Limit, MaxReadBytes)
	}
}

func TestAWriteThatFailsLeavesTheOldContents(t *testing.T) {
	_, granted, _ := grantedDir(t)
	target := filepath.Join(granted, "notes.txt")
	resolved, err := ResolvePath(target)
	if err != nil {
		t.Fatalf("ResolvePath: %v", err)
	}
	// Over the write limit: refused before anything is opened.
	err = WriteFile(resolved, make([]byte, MaxWriteBytes+1), 0o644)
	var tooLarge *ErrTooLarge
	if !errors.As(err, &tooLarge) {
		t.Fatalf("err = %v, want ErrTooLarge", err)
	}
	data, readErr := ReadFile(resolved)
	if readErr != nil {
		t.Fatalf("ReadFile: %v", readErr)
	}
	if string(data) != "hello" {
		t.Errorf("a refused write changed the file: %q", data)
	}
	// And no temporary was left behind.
	entries, err := os.ReadDir(granted)
	if err != nil {
		t.Fatalf("readdir: %v", err)
	}
	for _, e := range entries {
		if len(e.Name()) > 14 && e.Name()[:14] == ".cheese-write-" {
			t.Errorf("a refused write left a temporary behind: %s", e.Name())
		}
	}
}
