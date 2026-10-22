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
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/stallwatch"
)

// tool is one program the connector places from the server's toolchain route:
// the archive's suffix, and how to unpack it into a directory.
type tool struct {
	name    string
	suffix  string
	extract func(archive, dir string) error
}

// stallAfter is how long a fetch may receive nothing before it is given up and
// tried again. It is the same term the connector's own update uses, and for the
// same reason: these are tens of megabytes on a link that may be slow, so a
// deadline would fail the merely slow — but a transfer that has died must not
// hold the machine's enrolment for ever with nothing to show for it. A person
// watching this cannot tell those two apart, and until the attempt ends with an
// error neither can the connector.
var stallAfter = 60 * time.Second

// attempts is how many times one tool is fetched before the machine is told the
// runtime could not be placed. A transfer that dies mid-body is the ordinary
// case on a long link, not an exceptional one, and the whole of a tool's work
// is redone from its start either way — there is no resume, because the server
// serves these from a cache it may have re-fetched since.
const attempts = 3

// ensureTool fetches t when the server's digest differs from the one recorded
// beside the installed copy. The digest arrives in a header, so a runtime that
// is already current costs a response with its body left unread — and an
// attempt that ends without the tool placed is made again, because on a long
// link a transfer that dies is ordinary rather than exceptional.
func ensureTool(ctx context.Context, base, root, platform string, t tool, log io.Writer) error {
	url := base + "/toolchain/" + t.name + "/" + platform + "/artifact"
	dir := filepath.Join(root, t.name)
	marker := filepath.Join(root, t.name+".sha256")
	var last error
	for attempt := 1; attempt <= attempts; attempt++ {
		err := placeTool(ctx, url, root, dir, marker, t, log, attempt)
		if err == nil {
			return nil
		}
		last = err
		if ctx.Err() != nil {
			return last
		}
	}
	return fmt.Errorf("%s: %d attempts, last: %w", t.name, attempts, last)
}

// placeTool asks for t once and, if it is not already what this machine holds,
// downloads, verifies and unpacks it. Nothing but the verified result is left
// behind: an attempt that stopped arriving is given up on by `stallwatch`
// rather than held for ever, and its temp file removed, so the next attempt —
// or the next start of the connector — is not left to read a half archive.
//
// attempt is which one this is, 1-based; a retry says so rather than looking
// like the first attempt starting over.
func placeTool(ctx context.Context, url, root, dir, marker string, t tool, log io.Writer, attempt int) error {
	dctx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := http.NewRequestWithContext(dctx, http.MethodGet, url, nil)
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
	if attempt == 1 {
		fmt.Fprintf(log, "cheese: downloading %s for this machine…\n", t.name)
	} else {
		fmt.Fprintf(log, "cheese: downloading %s again (attempt %d of %d)…\n", t.name, attempt, attempts)
	}
	archive, err := os.CreateTemp(root, t.name+"-*"+t.suffix)
	if err != nil {
		return err
	}
	defer os.Remove(archive.Name())
	digest := sha256.New()
	if _, err := stallwatch.Copy(io.MultiWriter(archive, digest), resp.Body, stallAfter, cancel); err != nil {
		archive.Close()
		return err
	}
	if err := archive.Close(); err != nil {
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
