//go:build !windows

package host

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net"
	"os"
)

// dialExecutor reaches the resident executor for the state directory at
// resolved, over the Unix socket runtime.py names from the same digest.
func dialExecutor(ctx context.Context, resolved string) (net.Conn, error) {
	digest := sha256.Sum256([]byte(resolved))
	socket := fmt.Sprintf("/tmp/cheese-execution-%d-%x.sock", os.Getuid(), digest[:12])
	return (&net.Dialer{}).DialContext(ctx, "unix", socket)
}
