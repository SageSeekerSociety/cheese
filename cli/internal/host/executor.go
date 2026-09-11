package host

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/link"
)

// The authenticated server supplies the recorded executor state directory.
// Requests use the existing device WebSocket and the resident executor socket.
func executorExchange(ctx context.Context, state, input string, send func([]byte) error) error {
	resolved, err := filepath.EvalSymlinks(state)
	if err != nil {
		return err
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return err
	}
	digest := sha256.Sum256([]byte(resolved))
	socket := fmt.Sprintf("/tmp/cheese-execution-%d-%x.sock", os.Getuid(), digest[:12])
	conn, err := (&net.Dialer{}).DialContext(ctx, "unix", socket)
	if err != nil {
		return err
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	if _, err = io.WriteString(conn, input+"\n"); err != nil {
		return err
	}
	reader := bufio.NewReaderSize(conn, 64*1024)
	for {
		chunk, err := reader.ReadSlice('\n')
		if len(chunk) > 0 {
			if sendErr := send(chunk); sendErr != nil {
				return sendErr
			}
		}
		if err == nil {
			return nil
		}
		if err != bufio.ErrBufferFull {
			return fmt.Errorf("executor response interrupted: %w", err)
		}
	}
}

func (h *Host) runExecutor(m link.Msg) {
	timeout := time.Duration(m.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 660 * time.Second
	}
	ctx, cancel := context.WithTimeout(h.ctx, timeout)
	defer cancel()
	h.execMu.Lock()
	h.execs[m.ID] = cancel
	h.execMu.Unlock()
	defer func() { h.execMu.Lock(); delete(h.execs, m.ID); h.execMu.Unlock() }()
	err := executorExchange(ctx, m.Path, m.Stdin, func(data []byte) error {
		return h.conn.Send(link.Msg{T: "execution.data", ID: m.ID, Data: base64.StdEncoding.EncodeToString(data)})
	})
	result := link.Msg{T: "execution.result", ID: m.ID}
	if err != nil {
		result.Error = err.Error()
	}
	_ = h.conn.Send(result)
}
