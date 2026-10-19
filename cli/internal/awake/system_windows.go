package awake

import (
	"fmt"
	"golang.org/x/sys/windows"
	"runtime"
)

func AcquireSystem() (func(), error) {
	done, released := make(chan struct{}), make(chan struct{})
	ready := make(chan error)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		defer close(released)
		call := windows.NewLazySystemDLL("kernel32.dll").NewProc("SetThreadExecutionState")
		result, _, err := call.Call(0x80000001) // ES_CONTINUOUS | ES_SYSTEM_REQUIRED
		if result == 0 {
			ready <- fmt.Errorf("SetThreadExecutionState: %w", err)
			return
		}
		ready <- nil
		<-done
		_, _, _ = call.Call(0x80000000) // release on the same OS thread
	}()
	if err := <-ready; err != nil {
		return nil, err
	}
	return func() { close(done); <-released }, nil
}
