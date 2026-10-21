// Package awake holds a system idle-sleep assertion while AI work is running.
package awake

type Acquire func() (release func(), err error)

type Guard struct {
	acquire Acquire
	release func()
}

func New(acquire Acquire) *Guard { return &Guard{acquire: acquire} }

func (g *Guard) Update(enabled bool, sessions int) error {
	if !enabled || sessions == 0 {
		g.Close()
		return nil
	}
	if g.release == nil {
		release, err := g.acquire()
		if err != nil {
			return err
		}
		g.release = release
	}
	return nil
}

func (g *Guard) Close() {
	if g.release != nil {
		g.release()
		g.release = nil
	}
}
