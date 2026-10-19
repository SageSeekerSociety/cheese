//go:build !darwin

package claudecode

func relocateKeychain(source, destination string) (func() error, error) {
	return func() error { return nil }, nil
}
