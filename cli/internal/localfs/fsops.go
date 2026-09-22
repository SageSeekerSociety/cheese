package localfs

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// The bounds every operation runs inside. A granted directory is someone's real
// folder, and 不能被递归扫 is a property of the feature, not a nicety: `~/Documents`
// contains a home directory's worth of files, and an assistant that walks it
// without a limit is a denial of service the owner pays for on their own machine.
const (
	// MaxReadBytes is the most one read will return. Larger files are refused
	// rather than truncated, because a truncated file that looks complete is worse
	// than a refusal that names the size.
	MaxReadBytes = 8 << 20 // 8 MiB
	// MaxWriteBytes is the most one write will accept.
	MaxWriteBytes = 8 << 20
	// MaxListEntries caps one directory listing.
	MaxListEntries = 2000
	// MaxListDepth caps how far a recursive listing may descend. Depth 1 is the
	// directory itself, which is what a listing is by default.
	MaxListDepth = 4
)

// ErrTooLarge reports an operation that exceeded its bound, naming which one.
// Separate from a generic refusal because the caller can act on it — read it in
// pieces, or list a narrower directory.
type ErrTooLarge struct {
	What  string
	Limit int64
	Got   int64
}

func (e *ErrTooLarge) Error() string {
	return fmt.Sprintf("%s 超过上限（上限 %d，实际 %d）", e.What, e.Limit, e.Got)
}

// Entry is one name in a directory listing. Only metadata: a listing is for
// choosing what to read next, not for pulling a directory across the wire.
type Entry struct {
	Name  string `json:"name"`
	IsDir bool   `json:"is_dir"`
	Size  int64  `json:"size"`
	Mode  string `json:"mode"`
	// Symlink is set when the name is a link. A link is reported and never
	// followed during a listing: following one is how a walk leaves the granted
	// directory without ever being asked a question about a path outside it.
	Symlink bool `json:"symlink,omitempty"`
}

// ListOptions bounds and filters a listing.
type ListOptions struct {
	// Depth of 1 lists the directory itself; 0 is treated as 1. Never above
	// MaxListDepth.
	Depth int
	// Exclude skips any name that matches exactly, at every level. This is the
	// 「排除」 half of 大目录不能被递归扫: node_modules and .git are not browsable
	// material and are the bulk of the walk.
	Exclude []string
}

// ReadFile reads a whole file inside the granted directory.
//
// The path opened is the resolved one, not the text the caller wrote: the link
// that was just checked cannot be swapped for a different one between the check
// and the open, because what is opened is where the link led, not the link.
func ReadFile(resolved ResolvedPath) ([]byte, error) {
	if resolved.OnDisk == "" {
		return nil, errors.New("没有可读取的路径")
	}
	info, err := os.Lstat(resolved.OnDisk)
	if err != nil {
		return nil, err
	}
	if info.IsDir() {
		return nil, fmt.Errorf("%s 是一个目录", DisplayPath(resolved.OnDisk))
	}
	if !info.Mode().IsRegular() {
		// Not a regular file: a device, a socket, a fifo. Reading one is not
		// reading a document, and a fifo would simply block.
		return nil, fmt.Errorf("%s 不是普通文件", DisplayPath(resolved.OnDisk))
	}
	if info.Size() > MaxReadBytes {
		return nil, &ErrTooLarge{What: "文件", Limit: MaxReadBytes, Got: info.Size()}
	}
	f, err := os.Open(resolved.OnDisk)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	// Read one byte past the limit so a file that grew since the stat is caught
	// rather than silently truncated.
	data, err := io.ReadAll(io.LimitReader(f, MaxReadBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > MaxReadBytes {
		return nil, &ErrTooLarge{
			What:  "文件",
			Limit: MaxReadBytes,
			Got:   int64(len(data)),
		}
	}
	return data, nil
}

// WriteFile writes a file inside the granted directory, atomically.
//
// Written to a temporary name in the same directory and renamed into place, so a
// write that fails halfway leaves the previous contents rather than a half-written
// file the owner cannot tell from a complete one. The temporary is created 0600
// and only widened to perm on the way in, so the contents are never briefly
// world-readable.
func WriteFile(resolved ResolvedPath, data []byte, perm os.FileMode) error {
	if resolved.OnDisk == "" {
		return errors.New("没有可写入的路径")
	}
	if int64(len(data)) > MaxWriteBytes {
		return &ErrTooLarge{
			What:  "写入",
			Limit: MaxWriteBytes,
			Got:   int64(len(data)),
		}
	}
	if perm == 0 {
		perm = 0o644
	}
	dir := filepath.Dir(resolved.OnDisk)
	if info, err := os.Lstat(dir); err != nil {
		return err
	} else if !info.IsDir() {
		return fmt.Errorf("%s 不是目录", DisplayPath(dir))
	}
	if info, err := os.Lstat(resolved.OnDisk); err == nil && info.IsDir() {
		return fmt.Errorf("%s 是一个目录", DisplayPath(resolved.OnDisk))
	}

	tmp, err := os.CreateTemp(dir, ".cheese-write-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	// Best-effort cleanup; after a successful rename this is a no-op.
	defer func() { _ = os.Remove(tmpName) }()

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(perm); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, resolved.OnDisk)
}

// ListDir lists a directory inside the granted directory, bounded in both width
// and depth.
//
// Depth defaults to one level and never exceeds MaxListDepth. Symlinked names are
// reported and not descended into, so a walk cannot leave the granted directory
// by way of a link — it would have to be asked about the link's target as a path,
// and that question goes through Decide like every other one.
func ListDir(resolved ResolvedPath, opts ListOptions) ([]Entry, error) {
	if resolved.OnDisk == "" {
		return nil, errors.New("没有可列出的路径")
	}
	depth := opts.Depth
	if depth <= 0 {
		depth = 1
	}
	if depth > MaxListDepth {
		depth = MaxListDepth
	}
	excluded := make(map[string]struct{}, len(opts.Exclude))
	for _, name := range opts.Exclude {
		if name != "" {
			excluded[name] = struct{}{}
		}
	}

	out := make([]Entry, 0, 64)
	truncated := false
	var walk func(dir string, level int) error
	walk = func(dir string, level int) error {
		items, err := os.ReadDir(dir)
		if err != nil {
			if level == 1 {
				return err
			}
			// A subdirectory that cannot be read is skipped rather than failing the
			// whole listing: one permission-denied folder should not make the
			// granted directory unlistable.
			return nil
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Name() < items[j].Name() })
		for _, item := range items {
			if _, skip := excluded[item.Name()]; skip {
				continue
			}
			if len(out) >= MaxListEntries {
				truncated = true
				return nil
			}
			info, err := item.Info()
			if err != nil {
				continue
			}
			entry := Entry{
				Name:    item.Name(),
				IsDir:   item.IsDir(),
				Size:    info.Size(),
				Mode:    info.Mode().String(),
				Symlink: info.Mode()&fs.ModeSymlink != 0,
			}
			out = append(out, entry)
			if entry.IsDir && !entry.Symlink && level < depth {
				_ = walk(filepath.Join(dir, item.Name()), level+1)
			}
		}
		return nil
	}
	if err := walk(resolved.OnDisk, 1); err != nil {
		return nil, err
	}
	if truncated {
		return out, &ErrTooLarge{
			What:  "目录条目",
			Limit: MaxListEntries,
			Got:   int64(len(out)),
		}
	}
	return out, nil
}

// RelativeName renders an entry's path relative to the granted directory, so that
// what is shown and logged is the part inside the grant rather than the owner's
// absolute path.
func RelativeName(root, name string) string {
	rel, err := filepath.Rel(root, name)
	if err != nil || strings.HasPrefix(rel, "..") {
		return filepath.Base(name)
	}
	return rel
}
