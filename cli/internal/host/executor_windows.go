package host

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
)

// dialExecutor reaches the resident executor for the state directory at
// resolved. Python on Windows has no Unix sockets, so runtime.py listens on
// loopback and writes where, and the token it wants first, to
// executor.endpoint in that same directory; after the token line the
// exchange is the one every other platform has.
func dialExecutor(ctx context.Context, resolved string) (net.Conn, error) {
	data, err := os.ReadFile(filepath.Join(resolved, "executor.endpoint"))
	if err != nil {
		return nil, err
	}
	var endpoint struct {
		Port  int    `json:"port"`
		Token string `json:"token"`
	}
	if err := json.Unmarshal(data, &endpoint); err != nil || endpoint.Port <= 0 || endpoint.Token == "" {
		return nil, fmt.Errorf("executor endpoint unreadable: %v", err)
	}
	conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(endpoint.Port)))
	if err != nil {
		return nil, err
	}
	if _, err := io.WriteString(conn, endpoint.Token+"\n"); err != nil {
		conn.Close()
		return nil, err
	}
	return conn, nil
}
