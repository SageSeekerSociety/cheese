// Package stallwatch gives a download up when it stops arriving, rather than
// after a fixed total. An artifact here is tens of megabytes and a link may
// take minutes; a deadline fails the merely slow. What must not happen is a
// transfer that has died without saying so: it holds whoever waits for it —
// a person watching an enrolment, a connector's update loop — for ever, and to
// them a link that died and a link that is slow look exactly alike.
package stallwatch

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync/atomic"
	"time"
)

// ErrStalled marks a copy that was given up on because nothing arrived, so a
// caller can tell it from one that failed for a reason of its own.
var ErrStalled = errors.New("stalled")

// Copy copies src to dst, watching for progress and calling cancel — which must
// unblock src, in practice the request's context — once nothing has arrived for
// stall. A stall of zero copies without a watchdog.
//
// The error wraps ErrStalled when that is how it ended, with the stall and the
// bytes that did arrive, because those are the two things a reader of the
// failure needs and neither is recoverable afterwards.
func Copy(dst io.Writer, src io.Reader, stall time.Duration, cancel context.CancelFunc) (int64, error) {
	if stall <= 0 {
		return io.Copy(dst, src)
	}
	body := &watched{r: src, progress: make(chan struct{}, 1)}
	var stalled atomic.Bool
	copied := make(chan struct{})
	watching := make(chan struct{})
	go func() {
		defer close(watching)
		timer := time.NewTimer(stall)
		defer timer.Stop()
		for {
			select {
			case <-copied:
				return
			case <-body.progress:
				if !timer.Stop() {
					<-timer.C
				}
				timer.Reset(stall)
			case <-timer.C:
				stalled.Store(true)
				cancel()
				return
			}
		}
	}()
	n, err := io.Copy(dst, body)
	close(copied)
	<-watching
	if err != nil && stalled.Load() {
		return n, fmt.Errorf("%w: no data for %s after %d bytes", ErrStalled, stall, n)
	}
	return n, err
}

// watched counts what has arrived and tells the watchdog about it.
type watched struct {
	r        io.Reader
	progress chan struct{}
}

func (w *watched) Read(p []byte) (int, error) {
	n, err := w.r.Read(p)
	if n > 0 {
		select {
		case w.progress <- struct{}{}:
		default:
		}
	}
	return n, err
}
