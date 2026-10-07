package devenv

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// tool is one program the connector places from the server's toolchain route:
// the archive's suffix, and how to unpack it into a directory.
type tool struct {
	name    string
	suffix  string
	extract func(archive, dir string) error
}

// ensureTool fetches t when the server's digest differs from the one recorded
// beside the installed copy. The digest arrives in a header, so a runtime that
// is already current costs a response with its body left unread.
func ensureTool(ctx context.Context, base, root, platform string, t tool, log io.Writer) error {
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
	archive, err := os.CreateTemp(root, t.name+"-*"+t.suffix)
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
