// Package claudecode runs the platform's pinned Claude Code. Each enrollment
// keeps its own logins under its root's logins/ directory, apart from the
// machine owner's ~/.claude and every other enrollment.
//
// The owner adds one with the subscription command from the team's Models page.
// Every session of the owner's Claude Code on this machine keeps its own config
// directory and uses its login's directory as the credential store. The owner's
// own Claude Code never sees these logins.
package claudecode

import (
	"compress/gzip"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

// loginID is what a login may be called: the name of its directory, which a
// session's launch puts into a path. The backend's copy is `login_kind.LOGIN_ID`,
// held to this one by `backend/tests/unit/test_footprint_root.py`.
var loginID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// LoginDir is where the login this machine calls id lives.
func LoginDir(id string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("claude: locate home: %w", err)
	}
	if id == place.FirstClaudeLogin {
		return filepath.Join(home, place.CurrentRoot(), place.ClaudeLogin), nil
	}
	if !loginID.MatchString(id) {
		return "", fmt.Errorf("claude: %q is not a login on this machine", id)
	}
	return filepath.Join(home, place.CurrentRoot(), place.ClaudeLogins, id), nil
}

// NewLogin makes the directory of a login this machine does not have yet: the
// first one while there is none, else one with a new id.
func NewLogin() (string, string, error) {
	first, err := LoginDir(place.FirstClaudeLogin)
	if err != nil {
		return "", "", err
	}
	if _, err := os.Stat(first); errors.Is(err, os.ErrNotExist) {
		return place.FirstClaudeLogin, first, os.MkdirAll(first, 0o700)
	}
	id := rand.Text()
	dir, err := LoginDir(id)
	if err == nil {
		err = os.MkdirAll(dir, 0o700)
	}
	return id, dir, err
}

// Logins is every login this machine has, the first one first. Reading it
// reads no credentials.
func Logins() ([]string, error) {
	first, err := LoginDir(place.FirstClaudeLogin)
	if err != nil {
		return nil, err
	}
	var ids []string
	if info, err := os.Stat(first); err == nil && info.IsDir() {
		ids = append(ids, place.FirstClaudeLogin)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(filepath.Join(home, place.CurrentRoot(), place.ClaudeLogins))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("claude: %w", err)
	}
	for _, entry := range entries {
		if entry.IsDir() && loginID.MatchString(entry.Name()) {
			ids = append(ids, entry.Name())
		}
	}
	return ids, nil
}

// RemoveLogin forgets a login this machine was logged out of. The first one's
// directory stays, as it did when a machine had only that one.
func RemoveLogin(id string) error {
	if id == place.FirstClaudeLogin {
		return nil
	}
	dir, err := LoginDir(id)
	if err != nil {
		return err
	}
	if err := os.RemoveAll(dir); err != nil {
		return fmt.Errorf("claude: %w", err)
	}
	return nil
}

// vendorPlatform names a machine the way Anthropic's builds are published, the
// same names the platform's download route accepts (`claude_dist.PLATFORM_RE`).
func vendorPlatform(goos, goarch string, musl bool) (string, error) {
	var system string
	switch goos {
	case "linux":
		system = "linux"
	case "darwin":
		system = "darwin"
	case "windows":
		system = "win32"
	default:
		return "", fmt.Errorf("claude: no build for %s", goos)
	}
	var arch string
	switch goarch {
	case "amd64":
		arch = "x64"
	case "arm64":
		arch = "arm64"
	default:
		return "", fmt.Errorf("claude: no build for %s", goarch)
	}
	platform := system + "-" + arch
	if system == "linux" && musl {
		platform += "-musl"
	}
	return platform, nil
}

// usesMusl reports whether this Linux machine's C library is musl, which the
// vendor builds separately for.
func usesMusl() bool {
	matches, _ := filepath.Glob("/lib/ld-musl-*")
	return len(matches) > 0
}

func binaryName(platform string) string {
	if strings.HasPrefix(platform, "win32-") {
		return "claude.exe"
	}
	return "claude"
}

// origin is scheme://host of the stored server base: the connector's artifacts
// are served at the origin root, never under an edge prefix.
func origin(base string) (string, error) {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" {
		return "", fmt.Errorf("claude: bad server address %q", base)
	}
	return u.Scheme + "://" + u.Host, nil
}

// PinnedVersion asks the server which Claude Code build it runs.
func PinnedVersion(ctx context.Context, base string) (string, error) {
	root, err := origin(base)
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, root+"/connector/claude/pin", nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("claude: ask the server for its Claude Code version: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("claude: ask the server for its Claude Code version: HTTP %d", resp.StatusCode)
	}
	var answer struct {
		Version string `json:"version"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&answer); err != nil || answer.Version == "" {
		return "", errors.New("claude: the server did not name a Claude Code version")
	}
	return answer.Version, nil
}

// binaryPath is where the platform keeps a pinned build on this machine, the
// place the executor's bootstrap looks first (`remote_execution/bootstrap.binary`).
func binaryPath(version string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("claude: locate home: %w", err)
	}
	name := version
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	return filepath.Join(home, place.Root, "claude", "versions", name), nil
}

// Installed returns the pinned build when this machine already has it.
func Installed(ctx context.Context, base string) (string, bool, error) {
	version, err := PinnedVersion(ctx, base)
	if err != nil {
		return "", false, err
	}
	path, err := binaryPath(version)
	if err != nil {
		return "", false, err
	}
	return path, runsAs(path, version), nil
}

// A download is given up when nothing has arrived for stallAfter: the build is
// a few hundred megabytes and a slow link takes minutes, but one that stops
// moving is not going to finish. What arrived is kept for the next attempt.
var stallAfter = 60 * time.Second

// Ensure returns the pinned build, downloading it from the server first when
// this machine does not have it yet. onPercent, when not nil, hears how far the
// download has got, 0–100.
//
// The executor's bootstrap installs the same build into the same place
// (`remote_execution/executor_download.install`), and the desktop app runs this
// in the background while a session may be starting. So the two cooperate
// through the files beside the build: both hold `<build>.lock` while they
// install, and one that waited finds the build the other placed; both keep what
// has arrived in `<build>.partial`, in the same form, so either continues what
// the other was stopped in the middle of.
func Ensure(ctx context.Context, base string, progress io.Writer, onPercent func(int)) (string, error) {
	version, err := PinnedVersion(ctx, base)
	if err != nil {
		return "", err
	}
	destination, err := binaryPath(version)
	if err != nil {
		return "", err
	}
	if runsAs(destination, version) {
		return destination, nil
	}
	platform, err := vendorPlatform(runtime.GOOS, runtime.GOARCH, runtime.GOOS == "linux" && usesMusl())
	if err != nil {
		return "", err
	}
	root, err := origin(base)
	if err != nil {
		return "", err
	}
	source := fmt.Sprintf("%s/connector/claude/%s/%s/%s", root, version, platform, binaryName(platform))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	unlock, err := lockFile(destination + ".lock")
	if err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	defer unlock()
	if runsAs(destination, version) {
		return destination, nil
	}
	if progress != nil {
		fmt.Fprintf(progress, "Downloading Claude Code %s…\n", version)
	}
	temporary, err := download(ctx, source, destination, onPercent)
	if err != nil {
		return "", err
	}
	if err := os.Chmod(temporary, 0o700); err != nil {
		os.Remove(temporary)
		return "", fmt.Errorf("claude: %w", err)
	}
	if !runsAs(temporary, version) {
		os.Remove(temporary)
		return "", fmt.Errorf("claude: the downloaded build does not run as Claude Code %s", version)
	}
	if err := os.Rename(temporary, destination); err != nil {
		os.Remove(temporary)
		return "", fmt.Errorf("claude: %w", err)
	}
	return destination, nil
}

// download fetches source into a fresh file beside destination, checked against
// the digest the server names for it, continuing `<destination>.partial`. The
// packed copy is asked for and kept packed, so the offsets of every attempt —
// this one's and the bootstrap's — are into the same bytes; whether what
// arrived is packed is read off the file, since a resumed answer need not
// repeat its encoding. Bytes that do not add up to the digest are thrown away.
func download(ctx context.Context, source, destination string, onPercent func(int)) (string, error) {
	partial := destination + ".partial"
	var have int64
	if info, err := os.Stat(partial); err == nil {
		have = info.Size()
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	ask := func(start int64) (*http.Response, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, source, nil)
		if err != nil {
			return nil, err
		}
		// Named by hand, so the transport hands over the stream as it came.
		req.Header.Set("Accept-Encoding", "gzip")
		if start > 0 {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-", start))
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return nil, fmt.Errorf("claude: download %s: %w", source, err)
		}
		return resp, nil
	}
	start := have
	resp, err := ask(start)
	if err != nil {
		return "", err
	}
	if resp.StatusCode == http.StatusRequestedRangeNotSatisfiable {
		resp.Body.Close()
		// Nothing past what is held. Held at exactly the served copy's size it
		// is whole, and its last byte is asked for again for the digest the
		// answer carries; held longer, it is not a part of the copy served.
		_, served, _ := strings.Cut(resp.Header.Get("Content-Range"), "/")
		if served == strconv.FormatInt(have, 10) {
			start = have - 1
		} else {
			os.Remove(partial)
			have, start = 0, 0
		}
		if resp, err = ask(start); err != nil {
			return "", err
		}
	}
	defer resp.Body.Close()
	var total int64 = -1
	flags := os.O_CREATE | os.O_WRONLY
	switch resp.StatusCode {
	case http.StatusPartialContent:
		var first, last int64
		if _, err := fmt.Sscanf(resp.Header.Get("Content-Range"), "bytes %d-%d/%d", &first, &last, &total); err != nil || first != start {
			return "", fmt.Errorf("claude: download %s: the server resumed at %q", source, resp.Header.Get("Content-Range"))
		}
		flags |= os.O_APPEND
	case http.StatusOK:
		have, start = 0, 0
		total = resp.ContentLength
		flags |= os.O_TRUNC
	default:
		return "", fmt.Errorf("claude: download %s: HTTP %d", source, resp.StatusCode)
	}
	digest := strings.ToLower(resp.Header.Get("X-Checksum-SHA256"))
	if digest == "" {
		return "", fmt.Errorf("claude: download %s: the server named no checksum for it", source)
	}
	output, err := os.OpenFile(partial, flags, 0o600)
	if err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	// The byte asked for again is already held.
	if _, err := io.CopyN(io.Discard, resp.Body, have-start); err != nil {
		output.Close()
		return "", fmt.Errorf("claude: download %s: %w", source, err)
	}
	said := -1
	tell := func() {
		if onPercent == nil || total <= 0 {
			return
		}
		if now := int(have * 100 / total); now != said {
			said = now
			onPercent(now)
		}
	}
	tell()
	stall := time.AfterFunc(stallAfter, cancel)
	buffer := make([]byte, 1<<20)
	for {
		n, readErr := resp.Body.Read(buffer)
		if n > 0 {
			stall.Reset(stallAfter)
			if _, err := output.Write(buffer[:n]); err != nil {
				stall.Stop()
				output.Close()
				return "", fmt.Errorf("claude: %w", err)
			}
			have += int64(n)
			tell()
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			stall.Stop()
			output.Close()
			return "", fmt.Errorf("claude: download %s: %w", source, readErr)
		}
	}
	stall.Stop()
	if err := output.Close(); err != nil {
		return "", fmt.Errorf("claude: %w", err)
	}
	if total > 0 && have < total {
		return "", fmt.Errorf("claude: download %s: the connection closed early", source)
	}
	temporary, err := unpacked(partial, filepath.Dir(destination))
	if err == nil {
		err = matches(temporary, digest)
		if err != nil {
			os.Remove(temporary)
		}
	}
	if err != nil {
		os.Remove(partial)
		return "", fmt.Errorf("claude: the build downloaded from %s is not the one the server serves (%w); it was discarded", source, err)
	}
	return temporary, nil
}

// unpacked turns what arrived into the build, in a fresh file in dir: unpacked
// when it is the packed copy, as it is otherwise.
func unpacked(partial, dir string) (string, error) {
	held, err := os.Open(partial)
	if err != nil {
		return "", err
	}
	magic := make([]byte, 2)
	n, _ := io.ReadFull(held, magic)
	if _, err := held.Seek(0, io.SeekStart); err != nil {
		held.Close()
		return "", err
	}
	output, err := os.CreateTemp(dir, ".claude-download-*")
	if err != nil {
		held.Close()
		return "", err
	}
	path := output.Name()
	if n == 2 && magic[0] == 0x1f && magic[1] == 0x8b {
		var reader *gzip.Reader
		if reader, err = gzip.NewReader(held); err == nil {
			_, err = io.Copy(output, reader)
		}
		held.Close()
		if closeErr := output.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			os.Remove(path)
			return "", err
		}
		os.Remove(partial)
		return path, nil
	}
	held.Close()
	output.Close()
	if err := os.Rename(partial, path); err != nil {
		os.Remove(path)
		return "", err
	}
	return path, nil
}

// matches says whether the file at path hashes to digest.
func matches(path, digest string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return err
	}
	if got := hex.EncodeToString(hasher.Sum(nil)); got != digest {
		return fmt.Errorf("sha256 %s…, expected %.12s…", got[:12], digest)
	}
	return nil
}

// runsAs reports whether path is an executable that reports version.
func runsAs(path, version string) bool {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, path, "--version").Output()
	if err != nil {
		return false
	}
	fields := strings.Fields(string(out))
	return len(fields) > 0 && fields[0] == version
}

// inherited are the variables that would point Claude Code at another login or
// another config than the platform's: an inherited token wins over any stored
// login, and the platform names its own directories.
var inherited = []string{
	"CLAUDE_CONFIG_DIR",
	"CLAUDE_SECURESTORAGE_CONFIG_DIR",
	"CLAUDE_CODE_OAUTH_TOKEN",
	"CLAUDECODE",
	"CLAUDE_CODE_ENTRYPOINT",
}

// Environment is the environment Claude Code runs in for the platform's login:
// this process's, without what would redirect it, with the login directory as
// its config directory.
func Environment(base []string, loginDir string) []string {
	env := make([]string, 0, len(base)+2)
	hasUser := false
	for _, entry := range base {
		name, _, _ := strings.Cut(entry, "=")
		skip := false
		for _, drop := range inherited {
			if name == drop {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		if name == "USER" {
			hasUser = true
		}
		env = append(env, entry)
	}
	// On macOS the login is a Keychain item, and Claude Code started from a
	// launchd service has been reported unable to find it without USER
	// (anthropics/claude-code#77213); a service environment does not set it.
	if !hasUser {
		if current, err := user.Current(); err == nil && current.Username != "" {
			env = append(env, "USER="+current.Username)
		}
	}
	return append(env, "CLAUDE_CONFIG_DIR="+loginDir)
}

// Command runs the pinned build against the platform's login.
func Command(ctx context.Context, binary, loginDir string, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Env = Environment(os.Environ(), loginDir)
	cmd.Env = append(cmd.Env, "CLAUDE_SECURESTORAGE_CONFIG_DIR="+loginDir,
		"PATH="+filepath.Join(loginDir, place.ClaudeKeychain)+string(os.PathListSeparator)+os.Getenv("PATH"))
	return cmd
}

// Status is what `claude auth status` reports about the platform's login.
type Status struct {
	LoggedIn         bool   `json:"loggedIn"`
	AuthMethod       string `json:"authMethod"`
	SubscriptionType string `json:"subscriptionType"`
}

// ReadStatus asks the pinned build about the platform's login without calling a
// model.
func ReadStatus(ctx context.Context, binary, loginDir string) (Status, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	out, err := Command(ctx, binary, loginDir, "auth", "status").Output()
	var status Status
	if decodeErr := json.Unmarshal(out, &status); decodeErr != nil {
		if err != nil {
			return Status{}, fmt.Errorf("claude: auth status: %w", err)
		}
		return Status{}, fmt.Errorf("claude: auth status: %w", decodeErr)
	}
	return status, nil
}
