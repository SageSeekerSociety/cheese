// Package localfs is the device half of 本机目录授权: the folder on the user's own
// machine that the assistant has been granted, and the check that keeps it inside.
//
// The platform is not the enforcement point. The files are on this machine, so
// this process decides too, from its own copy of the grant set — a platform that
// is buggy, rolled back or merely lying must not be able to talk the daemon into
// touching a path nobody authorized. Neither side alone is trusted: the platform
// decides *before* it asks the device for anything, and the device decides again
// with the disk in front of it.
//
// paths.go is a deliberate mirror of backend/app/domain/local_fs/paths.py. The two
// must reach the same verdict, so they are held to the same table of cases in
// their tests (paths_test.go here, tests/unit/test_local_fs_paths.py there). If
// you change one, change both — a divergence is not a cosmetic problem, it is one
// side allowing what the other denies.
//
// The rule the whole file exists to enforce: normalize first, compare after, and
// compare over *segments* — never over a string prefix. startswith is not
// containment (`MyDocs` is a prefix of `MyDocuments/secret.txt`), and the string
// that arrives is not the string the filesystem resolves (`..`, relative, `~`,
// case, Win32's trailing dots and reserved device names).
package localfs

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

// Platform is the filesystem convention of the machine a path lives on — not the
// convention of the machine this code runs on. A device is one of these.
type Platform string

const (
	PlatformWindows Platform = "windows"
	PlatformMacOS   Platform = "macos"
	PlatformLinux   Platform = "linux"
)

// CaseInsensitive reports whether two paths differing only in case name the same
// file. Windows and macOS both default to a case-insensitive, case-preserving
// filesystem; Linux does not. Folding the comparison key on the first two is what
// keeps a grant from being bypassed by re-casing the path.
func (p Platform) CaseInsensitive() bool { return p != PlatformLinux }

// Valid reports whether the value is one this package knows how to normalize for.
// An unknown platform must not silently default to either behaviour.
func (p Platform) Valid() bool {
	switch p {
	case PlatformWindows, PlatformMacOS, PlatformLinux:
		return true
	}
	return false
}

// A path longer than this is refused rather than truncated: truncation would make
// two different paths compare equal, which is the one outcome this file exists to
// prevent. 4096 is the modern ceiling and comfortably above anything a person
// authorizes by hand.
const MaxPathLength = 4096

// Depth cap. Also the bound that keeps a recursive listing from being unbounded —
// the listing limit itself is much smaller and lives at the call site.
const MaxSegments = 256

// Refusal is a path this package will not normalize, carrying a reason code.
//
// Every refusal is a named code rather than a bare failure, because the reason is
// shown to the person whose file was refused and written to the audit log.
// 拒绝要让人看得见 — a refusal nobody can explain is indistinguishable from a bug.
type Refusal struct {
	Reason string
	Detail string
	Raw    string
}

func (r *Refusal) Error() string { return r.Reason + ": " + r.Detail }

func refuse(reason, detail, raw string) error {
	return &Refusal{Reason: reason, Detail: detail, Raw: raw}
}

// AsRefusal reports whether err is one of this package's named refusals.
func AsRefusal(err error) (*Refusal, bool) {
	var r *Refusal
	if errors.As(err, &r) {
		return r, true
	}
	return nil, false
}

// NormalizedPath is a path reduced to something two paths can be compared by.
//
// Root is the anchor `..` may never climb past — `/` for POSIX, a drive (`C:`) or a
// UNC share (`//server/share`) for Windows. Segments holds the path below it,
// already dot-free. Key is the single string a database and a human-readable
// comparison both use; it is not what the filesystem is asked to open (that is the
// device's own resolved text, handled in host.go).
type NormalizedPath struct {
	Platform Platform
	Root     string
	Segments []string
	// FoldedSegments is Segments folded the way this filesystem compares names.
	// Contains reads THIS, not Segments: on a case-insensitive filesystem two
	// names differing only in case are one file, and a containment check over the
	// unfolded segments would deny a legitimate access while looking correct.
	FoldedSegments []string
	// Text is the canonical text: root + segments, case preserved. This is what
	// the UI shows.
	Text string
	// Key is Text case-folded on case-insensitive platforms.
	Key string
}

// IsRoot reports whether this path is its own anchor — `/` or `C:/`. A grant may
// never point at one; see Service.Grant.
func (n NormalizedPath) IsRoot() bool { return len(n.Segments) == 0 }

// fold is Python's str.casefold() — Unicode *full* case folding, not the simple
// lowercase Go's strings.ToLower performs. The difference is real and reachable:
// casefold turns ß into ss and ﬀ into ff, ToLower leaves both alone. Using the
// simple mapping here would make this file disagree with paths.py on exactly the
// names an attacker would choose, so the cases package it is.
var fold = cases.Fold()

// upper is Python's str.upper(): the full uppercase mapping, which can change
// length (ß -> SS). Only ever applied to a single drive letter, but the wrong
// mapping there is a wrong root.
var upper = cases.Upper(language.Und)

var reservedWindowsNames = func() map[string]struct{} {
	names := []string{"con", "prn", "aux", "nul"}
	for d := 1; d <= 9; d++ {
		names = append(names, fmt.Sprintf("com%d", d), fmt.Sprintf("lpt%d", d))
	}
	set := make(map[string]struct{}, len(names))
	for _, n := range names {
		set[n] = struct{}{}
	}
	return set
}()

// Normalize reduces raw to a NormalizedPath, or refuses it by name.
//
// The order of the steps is the security property, not an implementation detail.
// Separators are folded first so one spelling of a path cannot dodge a check
// written against another; `..` is resolved second, before anything compares
// segments; case is folded last, so the key two callers compare is already in the
// form the filesystem would have used.
func Normalize(raw string, platform Platform) (NormalizedPath, error) {
	var zero NormalizedPath
	if !platform.Valid() {
		return zero, refuse("unknown_platform", "未知的文件系统平台", raw)
	}
	if strings.TrimSpace(raw) == "" {
		return zero, refuse("empty", "路径为空", raw)
	}
	if hasControlChar(raw) {
		return zero, refuse("control_char", "路径含控制字符", raw)
	}
	if utf8.RuneCountInString(raw) > MaxPathLength {
		return zero, refuse("too_long",
			fmt.Sprintf("路径超过 %d 个字符", MaxPathLength), raw)
	}

	var (
		root     string
		segments []string
		err      error
	)
	if platform == PlatformWindows {
		root, segments, err = normalizeWindows(raw)
	} else {
		root, segments, err = normalizePOSIX(raw)
	}
	if err != nil {
		return zero, err
	}

	if len(segments) > MaxSegments {
		return zero, refuse("too_deep",
			fmt.Sprintf("路径超过 %d 层", MaxSegments), raw)
	}

	text := join(root, segments)
	key := text
	folded := segments
	if platform.CaseInsensitive() {
		key = fold.String(text)
		folded = make([]string, len(segments))
		for i, s := range segments {
			folded[i] = fold.String(s)
		}
	}
	return NormalizedPath{
		Platform:       platform,
		Root:           root,
		Segments:       segments,
		FoldedSegments: folded,
		Text:           text,
		Key:            key,
	}, nil
}

// hasControlChar reports NUL and the C0/C1 control range, which no filesystem in
// scope accepts. Written as a scan rather than a regexp on purpose: a regexp would
// need the same escapes here and in paths.py, and the two escaping dialects are
// exactly where a security check silently stops matching.
func hasControlChar(text string) bool {
	for _, ch := range text {
		if ch < 0x20 || ch == 0x7F {
			return true
		}
	}
	return false
}

// normalizeWindows handles a UNC share or a drive-letter root; nothing else is
// absolute enough to authorize.
func normalizeWindows(raw string) (string, []string, error) {
	// One spelling. A caller who writes the backslash form and one who writes the
	// forward-slash form must land on the same grant, or the second spelling is a
	// way to reach a path the first spelling's check would have refused.
	text := strings.ReplaceAll(raw, `\`, "/")

	// The `//?/` and `//./` prefixes (the Win32 device namespaces) reach the
	// filesystem through a different parser — one that does NOT strip trailing
	// dots and does not resolve `..`. Refusing them outright is the only way to
	// keep this file's assumption true: that Win32's own normalization agrees with
	// ours.
	if strings.HasPrefix(text, "//?/") || strings.HasPrefix(text, "//./") {
		return "", nil, refuse("device_prefix", "不支持 Win32 设备命名空间前缀", raw)
	}

	if strings.HasPrefix(text, "//") {
		// UNC `//server/share/rest`. Server and share are the root; `..` must not
		// be able to climb above the share into another share on the server.
		parts := splitSegments(text[2:])
		if len(parts) < 2 {
			return "", nil, refuse("unc_incomplete", "UNC 路径缺少共享名", raw)
		}
		server, share, rest := parts[0], parts[1], parts[2:]
		if server == "" || share == "" {
			return "", nil, refuse("unc_incomplete", "UNC 路径缺少服务器或共享名", raw)
		}
		root := "//" + server + "/" + share
		segments, err := collapse(rest, root, raw, true)
		if err != nil {
			return "", nil, err
		}
		return root, segments, nil
	}

	first, size := utf8.DecodeRuneInString(text)
	rest := text[size:]
	if !unicode.IsLetter(first) || !strings.HasPrefix(rest, ":") {
		// No drive letter. `/Users/alice` and `Users/alice` are both refused: the
		// first is root-relative (it means "on whatever drive the process is on",
		// which is not a fact this package knows) and the second is relative to a
		// working directory that is not a fact either. A relative path cannot be
		// authorized, because the directory it denotes is not fixed.
		return "", nil, refuse("not_absolute",
			"Windows 路径必须以盘符或 UNC 共享开头", raw)
	}
	letter := upper.String(string(first))
	rest = rest[1:] // drop the colon
	if !strings.HasPrefix(rest, "/") {
		// `C:foo` is drive-relative — it means "foo under the current directory of
		// drive C", whose location depends on per-drive process state. Two runs of
		// the same request can name two different files, so it cannot be
		// authorized at all.
		return "", nil, refuse("drive_relative", "不支持盘符相对路径（如 C:foo）", raw)
	}
	root := letter + ":"
	segments, err := collapse(splitSegments(rest), root, raw, true)
	if err != nil {
		return "", nil, err
	}
	return root, segments, nil
}

// normalizePOSIX accepts an absolute `/`-rooted path, or nothing.
func normalizePOSIX(raw string) (string, []string, error) {
	if !strings.HasPrefix(raw, "/") {
		// Same reasoning as Windows' not_absolute: `Documents`, `./docs` and
		// `../x` all denote a directory that depends on where the process happens
		// to be standing, so none of them can be authorized. The tilde is included
		// here on purpose — the shell expands it, the kernel does not, and a device
		// that received it would either fail or (worse) expand it to a home
		// directory the platform never agreed to. Resolve it on the device first;
		// see ResolveLocalPath.
		return "", nil, refuse("not_absolute",
			"路径必须是绝对路径（~ 与相对路径请在设备上解析为绝对路径）", raw)
	}
	// POSIX allows exactly two leading slashes to be implementation-defined; one
	// slash or three-or-more are the same as one. Since a grant is compared by
	// segment, collapsing `//` to `/` here keeps `//etc` from becoming a second,
	// unrefused spelling of `/etc`.
	stripped := strings.TrimLeft(raw, "/")
	segments, err := collapse(splitSegments(stripped), "/", raw, false)
	if err != nil {
		return "", nil, err
	}
	return "/", segments, nil
}

// splitSegments is the caller-side filter in paths.py: split on `/` and drop the
// empty and `.` pieces. `..` is deliberately left for collapse to resolve.
func splitSegments(text string) []string {
	parts := strings.Split(text, "/")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p == "" || p == "." {
			continue
		}
		out = append(out, p)
	}
	return out
}

// collapse resolves `.` and `..` lexically and refuses a climb above root.
//
// `..` is popped against the segments already accepted, which is the same thing
// the filesystem does for a path with no symlinks in it. When there is nothing
// left to pop, the path is trying to leave the anchor it is measured against — and
// that is refused rather than clamped. Clamping is the tempting behaviour and the
// dangerous one: it turns `/granted/../../etc` into `/etc`, i.e. it answers a
// refusal-shaped question with a silent success on some *other* directory.
func collapse(parts []string, root, raw string, windows bool) ([]string, error) {
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if part == "." {
			continue
		}
		if part == ".." {
			if len(out) == 0 {
				return nil, refuse("escapes_root", "路径向上越过了根 "+root, raw)
			}
			out = out[:len(out)-1]
			continue
		}
		if windows {
			folded, err := foldWindowsSegment(part, raw)
			if err != nil {
				return nil, err
			}
			part = folded
		}
		if part == "" {
			continue
		}
		out = append(out, part)
	}
	return out, nil
}

// foldWindowsSegment applies Win32's own segment rewrites so our idea of the path
// matches its.
//
// Two rewrites matter enough to implement, because both name a file other than the
// one the raw string appears to name:
//
//   - Trailing dots and spaces are stripped. `MyDocs.` and `MyDocs ` (with a
//     trailing blank) both reach `MyDocs`. A check that compared the raw segment
//     would see a different name than the one opened.
//   - Reserved device names name devices. `NUL`, `CON`, `COM1` … are devices
//     whether or not an extension follows, so `NUL.txt` is not a file under the
//     directory and must not be treated as one.
func foldWindowsSegment(segment, raw string) (string, error) {
	stripped := strings.TrimRight(segment, " .")
	if stripped == "" {
		return "", refuse("empty_segment", "段在去掉结尾的点与空格后为空", raw)
	}
	stem := stripped
	if i := strings.Index(stripped, "."); i >= 0 {
		stem = stripped[:i]
	}
	if _, bad := reservedWindowsNames[fold.String(stem)]; bad {
		return "", refuse("reserved_name",
			"「"+stripped+"」是 Windows 保留设备名", raw)
	}
	return stripped, nil
}

func join(root string, segments []string) string {
	switch {
	case root == "/":
		if len(segments) == 0 {
			return "/"
		}
		return "/" + strings.Join(segments, "/")
	case strings.HasPrefix(root, "//"):
		if len(segments) == 0 {
			return root
		}
		return root + "/" + strings.Join(segments, "/")
	default:
		// A drive root keeps its slash: `C:/` is the drive, `C:` is drive-relative.
		if len(segments) == 0 {
			return root + "/"
		}
		return root + "/" + strings.Join(segments, "/")
	}
}

// Contains reports whether inner is outer itself or lies beneath it.
//
// Segment-wise, so the `MyDocs` / `MyDocuments` confusion cannot be expressed. The
// roots must match and outer's folded segments must be a prefix of inner's. Both
// sides must already be normalized — there is no raw-string overload, deliberately,
// because the raw-string version is the bug.
//
// The comparison runs on FoldedSegments, so it asks the question the filesystem
// would answer: on Windows and macOS `C:/Users/Alice/MyDocs` does contain
// `c:/users/alice/mydocs/x`, and on Linux it does not.
//
// Two paths on different kinds of filesystem are never comparable, so a mismatched
// platform pair answers false rather than picking one side's rules.
func Contains(outer, inner NormalizedPath) bool {
	if outer.Platform != inner.Platform {
		return false
	}
	if foldedRoot(outer) != foldedRoot(inner) {
		return false
	}
	if len(inner.FoldedSegments) < len(outer.FoldedSegments) {
		return false
	}
	for i, seg := range outer.FoldedSegments {
		if inner.FoldedSegments[i] != seg {
			return false
		}
	}
	return true
}

func foldedRoot(p NormalizedPath) string {
	if p.Platform.CaseInsensitive() {
		return fold.String(p.Root)
	}
	return p.Root
}
