//go:build !windows

// Package devenv places what the server's commands expect to find on a machine
// where the system does not already provide it. Two kinds:
//
// A program the platform's own work runs on, where any build new enough will
// do: `python3` everywhere (the session's runner and helpers need 3.11, and a
// Mac's /usr/bin/python3 is 3.9), and on Linux `tmux` (the connector keeps
// every screen in it) and `git` (every room's checkout). The system's copy is
// used when it is new enough; otherwise the server's pinned build is placed
// under the footprint root and put first on this process's PATH, which every
// command and session it starts inherits. Once placed, it stays the one used.
//
// What a room's isolation is made with, placed on Linux whatever the system
// has, because the sandbox is the platform's and so is the build it runs on:
// `bwrap`, and the host tools a platform host's sandboxes get their network
// and disk limits with as root (`ip`, `iptables`, `xfs_quota`; `mkfs.xfs`
// formats the disk they live on). The host tools are not put on PATH: the
// root helper runs root's own copies of them by absolute path
// (`remote_execution/bootstrap.sandbox_tools`).
package devenv

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
	"github.com/SageSeekerSociety/cheese/cli/internal/runtimepath"
)

// fallback is a program used from the system when the system's is new enough,
// and placed from the server's toolchain otherwise, at <root>/<tool>/bin.
type fallback struct {
	tool    string
	program string
	usable  func() bool
}

func fallbacks() []fallback {
	list := []fallback{{"python", "python3", pythonRecentEnough}}
	if runtime.GOOS == "linux" {
		list = append(list,
			// pane_dead_time, which the connector reads to close a dead
			// screen, is in tmux from 3.3.
			fallback{"tmux", "tmux", func() bool { return atLeast("tmux", []string{"-V"}, 3, 3) }},
			// `rev-parse --path-format=absolute`, which a room's checkout
			// asks, is in git from 2.31.
			fallback{"git", "git", func() bool { return atLeast("git", []string{"--version"}, 2, 31) }},
		)
	}
	return list
}

// Ensure places what this machine lacks (see the package's text) and puts
// each placed directory first on PATH. base is the connector's
// `<origin>/connector`.
func Ensure(ctx context.Context, base string, log io.Writer) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	root := filepath.Join(home, place.CurrentRoot(), "runtime")
	var needed []fallback
	for _, f := range fallbacks() {
		_, placed := os.Stat(filepath.Join(root, f.tool, "bin", f.program))
		if placed == nil || !f.usable() {
			needed = append(needed, f)
		}
	}
	if len(needed) == 0 && runtime.GOOS != "linux" {
		return nil
	}
	platform, err := toolchainPlatform(runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return err
	}
	base = strings.TrimRight(base, "/")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	// A tool the server cannot be asked about now (a machine starting before
	// its network) keeps the copy placed before, which was checked then.
	var failed error
	place := func(name, onPath string) {
		if err := ensureTool(ctx, base, root, platform, tool{name, ".tar.gz", extractTarGz}, log); err != nil {
			failed = errors.Join(failed, fmt.Errorf("%s: %w", name, err))
			if _, err := os.Stat(filepath.Join(root, name)); err != nil {
				return
			}
		}
		if onPath != "" {
			runtimepath.Put(onPath)
		}
	}
	// What has a copy on the machine either way, and whose absence costs a
	// feature rather than the machine: reported to the log, never returned.
	// `Ensure`'s error is fatal where it is called — `cheesehost connect`
	// returns it and stops installing the service (`daemoncmd.model_login`) —
	// so a transient 503 on the toolchain route, or a release not fetched yet,
	// must not travel as one. The copy placed before is kept if the fetch
	// fails, which `ensureTool` does, so a machine that had it keeps it.
	placeOptional := func(name string) {
		if err := ensureTool(ctx, base, root, platform, tool{name, ".tar.gz", extractTarGz}, log); err != nil {
			fmt.Fprintf(log, "%s: %v (not required; the machine runs without it)\n", name, err)
		}
	}
	if runtime.GOOS == "linux" {
		place("bubblewrap", filepath.Join(root, "bubblewrap"))
		place("host-tools", "")
		// A shared library, not a program: nothing is put on PATH. A room's
		// executor points `fusepy` at `runtime/fuse-runtime/libfuse.so.2` by
		// path (`bootstrap.placed_libfuse`), and a machine that cannot get it
		// simply has no transcript view — the room runs as it did before. That
		// is why it is placed as optional and not through `place`.
		placeOptional("fuse-runtime")
	}
	for _, f := range needed {
		place(f.tool, filepath.Join(root, f.tool, "bin"))
	}
	return failed
}

// minimum is the oldest Python the session's runner runs on (it uses StrEnum).
const minimum = "(3, 11)"

// pythonRecentEnough reports whether the python3 on PATH runs and is new enough.
func pythonRecentEnough() bool {
	check := fmt.Sprintf("import sys; sys.exit(0 if sys.version_info >= %s else 1)", minimum)
	return exec.Command("python3", "-c", check).Run() == nil
}

var version = regexp.MustCompile(`(\d+)\.(\d+)`)

// atLeast reports whether program, asked for its version with args, runs and
// names a version of at least major.minor. One that names none is not trusted.
func atLeast(program string, args []string, major, minor int) bool {
	out, err := exec.Command(program, args...).Output()
	if err != nil {
		return false
	}
	found := version.FindStringSubmatch(string(out))
	if found == nil {
		return false
	}
	gotMajor, _ := strconv.Atoi(found[1])
	gotMinor, _ := strconv.Atoi(found[2])
	return gotMajor > major || gotMajor == major && gotMinor >= minor
}

// toolchainPlatform names this machine as the server's toolchain route does.
func toolchainPlatform(goos, goarch string) (string, error) {
	arch := map[string]string{"amd64": "x64", "arm64": "arm64"}[goarch]
	if (goos != "linux" && goos != "darwin") || arch == "" {
		return "", fmt.Errorf("no toolchain build for %s/%s", goos, goarch)
	}
	return goos + "-" + arch, nil
}

// extractTarGz unpacks a toolchain archive into dir, dropping
// its top directory (`python/`, `git-1/`…), so a program is at dir/bin/<name>.
// Nothing may land outside dir, by name or through a link.
func extractTarGz(archive, dir string) error {
	file, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer file.Close()
	unzipped, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	reader := tar.NewReader(unzipped)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		name := path.Clean(header.Name)
		if name == ".." || strings.HasPrefix(name, "../") || path.IsAbs(name) {
			return fmt.Errorf("unpack: %q is outside the archive", header.Name)
		}
		_, rest, _ := strings.Cut(name, "/")
		if rest == "" {
			continue
		}
		target := filepath.Join(dir, filepath.FromSlash(rest))
		if !inside(dir, target) {
			return fmt.Errorf("unpack: %q is outside the archive", header.Name)
		}
		mode := os.FileMode(header.Mode).Perm()
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
			if err != nil {
				return err
			}
			_, err = io.Copy(out, reader)
			out.Close()
			if err != nil {
				return err
			}
		case tar.TypeSymlink:
			if !inside(dir, filepath.Join(filepath.Dir(target), header.Linkname)) {
				return fmt.Errorf("unpack: %q links outside the archive", header.Name)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			if err := os.Symlink(header.Linkname, target); err != nil {
				return err
			}
		}
	}
}

func inside(dir, target string) bool {
	rel, err := filepath.Rel(dir, target)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
