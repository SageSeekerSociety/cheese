// Package devenv places what the server's commands expect to find on a
// Windows machine: `python3` for the executor and the scripts it runs, and a
// POSIX shell with git, curl and coreutils (Git for Windows, which Claude Code
// on Windows needs anyway). Both come from the server's toolchain route, pinned
// and checksummed there, and live under the footprint root, so uninstall takes
// them with everything else. The connector puts them first on its own PATH,
// which every command it runs inherits — so the argv the server already sends
// (`python3 -`, `sh -c …`, `git …`) resolves here unchanged.
package devenv

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

const platform = "windows-x64"

type tool struct {
	name    string
	extract func(archive, dir string) error
}

var tools = []tool{
	{"python", extractPython},
	{"git", extractGit},
}

// Ensure makes the runtime current against the server at base (the
// connector's `<origin>/connector`) and puts it on this process's PATH.
func Ensure(ctx context.Context, base string, log io.Writer) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	root := filepath.Join(home, place.Root, "runtime")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	for _, t := range tools {
		if err := ensureTool(ctx, strings.TrimRight(base, "/"), root, t, log); err != nil {
			return fmt.Errorf("%s: %w", t.name, err)
		}
	}
	git := filepath.Join(root, "git")
	path := []string{
		filepath.Join(root, "python"),
		filepath.Join(git, "cmd"),
		filepath.Join(git, "usr", "bin"),
		filepath.Join(git, "mingw64", "bin"),
	}
	current := os.Getenv("PATH")
	if !strings.HasPrefix(current, path[0]+";") {
		os.Setenv("PATH", strings.Join(append(path, current), ";"))
	}
	os.Setenv("CLAUDE_CODE_GIT_BASH_PATH", filepath.Join(git, "bin", "bash.exe"))
	return nil
}

// ensureTool fetches t when the server's digest differs from the one recorded
// beside the installed copy. The digest arrives in a header, so a runtime that
// is already current costs a response with its body left unread.
func ensureTool(ctx context.Context, base, root string, t tool, log io.Writer) error {
	dir := filepath.Join(root, t.name)
	marker := filepath.Join(root, t.name+".sha256")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/toolchain/"+t.name+"/"+platform+"/artifact", nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download: HTTP %d", resp.StatusCode)
	}
	want := strings.ToLower(resp.Header.Get("X-Checksum-SHA256"))
	if want == "" {
		return fmt.Errorf("the server sent no checksum")
	}
	if have, err := os.ReadFile(marker); err == nil && strings.TrimSpace(string(have)) == want {
		if _, err := os.Stat(dir); err == nil {
			return nil
		}
	}
	fmt.Fprintf(log, "cheese: downloading %s for this machine…\n", t.name)
	archive, err := os.CreateTemp(root, t.name+"-*"+suffix(t.name))
	if err != nil {
		return err
	}
	defer os.Remove(archive.Name())
	digest := sha256.New()
	_, err = io.Copy(io.MultiWriter(archive, digest), resp.Body)
	archive.Close()
	if err != nil {
		return err
	}
	if got := hex.EncodeToString(digest.Sum(nil)); got != want {
		return fmt.Errorf("checksum %s, expected %s", got, want)
	}
	staged := dir + ".new"
	os.RemoveAll(staged)
	if err := os.MkdirAll(staged, 0o700); err != nil {
		return err
	}
	if err := t.extract(archive.Name(), staged); err != nil {
		os.RemoveAll(staged)
		return err
	}
	// A previous copy may be in use by a command still running; if it cannot
	// be removed now the new one waits for the next start.
	if err := os.RemoveAll(dir); err != nil {
		os.RemoveAll(staged)
		return err
	}
	if err := os.Rename(staged, dir); err != nil {
		return err
	}
	return os.WriteFile(marker, []byte(want+"\n"), 0o600)
}

func suffix(name string) string {
	if name == "git" {
		return ".exe" // a self-extracting archive, run to unpack
	}
	return ".zip"
}

// extractPython unpacks the embeddable distribution with Windows' own tar,
// names it the way the server's commands call it, and drops its ._pth: that
// file pins sys.path and silently ignores PYTHONPATH and site-packages, which
// would make it behave unlike the python3 every other device has.
func extractPython(archive, dir string) error {
	if out, err := hidden("tar.exe", "-xf", archive, "-C", dir).CombinedOutput(); err != nil {
		return fmt.Errorf("unpack: %v: %s", err, out)
	}
	if err := copyFile(filepath.Join(dir, "python.exe"), filepath.Join(dir, "python3.exe")); err != nil {
		return err
	}
	pth, _ := filepath.Glob(filepath.Join(dir, "python*._pth"))
	for _, p := range pth {
		if err := os.Remove(p); err != nil {
			return err
		}
	}
	return nil
}

// extractGit runs PortableGit's self-extractor into dir.
func extractGit(archive, dir string) error {
	cmd := hidden(archive)
	// The extractor takes its destination as -o"<dir>", quote after the -o;
	// the default quoting would wrap the whole argument, and a profile path
	// with a space in it is ordinary.
	cmd.SysProcAttr.CmdLine = fmt.Sprintf(`"%s" -y -o"%s"`, archive, dir)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("unpack: %v: %s", err, out)
	}
	if _, err := os.Stat(filepath.Join(dir, "bin", "bash.exe")); err != nil {
		return fmt.Errorf("unpacked, but no bash: %w", err)
	}
	return nil
}

func copyFile(from, to string) error {
	data, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return os.WriteFile(to, data, 0o755)
}

func hidden(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	return cmd
}
