package daemoncmd

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	"github.com/SageSeekerSociety/cheese/cli/internal/claudecode"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/ui"
)

// claudeCmd is the owner's own Claude Code as the platform runs it: logged in
// once here, under a directory of the platform's, and used by every session of
// the owner's Claude Code on this machine. The owner's own `~/.claude` is not
// read or touched.
func claudeCmd(cfgPath *string, withConfig func(*cobra.Command) *cobra.Command) *cobra.Command {
	var console bool
	var serviceURL, serviceModel string
	login := withConfig(&cobra.Command{
		Use:   "login",
		Short: "Log in the Claude Code the platform runs on this machine",
		Long: "Installs the Claude Code build the platform runs, if this machine does not\n" +
			"have it yet, and logs it in with your own Claude account or API key. The\n" +
			"login is kept apart from your own Claude Code (~/.claude), which keeps its\n" +
			"own login and settings. The login opens in the browser; with no terminal\n" +
			"attached it finishes there, so the desktop app can run it too.\n\n" +
			"With --base-url and --model it uses another model service that speaks\n" +
			"Anthropic's API instead (GLM, Kimi, DeepSeek, a relay of your own). Its key\n" +
			"is read from " + modelTokenEnv + ", or asked for. It stays on this machine.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			if serviceURL != "" || serviceModel != "" {
				return useModelService(*cfgPath, serviceURL, serviceModel)
			}
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, err := claudecode.Ensure(ctx, base, os.Stdout)
			if err != nil {
				return err
			}
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			if err := os.MkdirAll(loginDir, 0o700); err != nil {
				return err
			}
			// The latest choice wins: logging in with an account stops using a
			// model service set before.
			if err := claudecode.ForgetModelService(loginDir); err != nil {
				return err
			}
			kind := "--claudeai"
			if console {
				kind = "--console"
			}
			cmd := claudecode.Command(ctx, binary, loginDir, "auth", "login", kind)
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("claude: login did not finish: %w", err)
			}
			return printClaudeStatus(ctx, binary, loginDir)
		},
	})

	login.Flags().BoolVar(&console, "console", false,
		"log in with an Anthropic Console account (API usage billing) instead of a Claude subscription")
	login.Flags().StringVar(&serviceURL, "base-url", "",
		"use the model service at this address instead of a Claude account")
	login.Flags().StringVar(&serviceModel, "model", "", "the model to call on the model service")

	status := withConfig(&cobra.Command{
		Use:   "status",
		Short: "Show whether the platform's Claude Code on this machine is logged in",
		Args:  cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, installed, err := claudecode.Installed(ctx, base)
			if err != nil {
				return err
			}
			if !installed {
				ui.Warn("Claude Code is not set up on this machine yet.")
				ui.Hint("`cheesehost claude login` to install and log it in")
				return nil
			}
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			return printClaudeStatus(ctx, binary, loginDir)
		},
	})

	logout := withConfig(&cobra.Command{
		Use:   "logout",
		Short: "Log out the platform's Claude Code on this machine",
		Long: "Logs out the Claude Code the platform runs and forgets its model service.\n" +
			"Your own Claude Code (~/.claude) stays logged in.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			loginDir, err := claudecode.LoginDir()
			if err != nil {
				return err
			}
			if err := claudecode.ForgetModelService(loginDir); err != nil {
				return err
			}
			base, err := serverBase(*cfgPath)
			if err != nil {
				return err
			}
			ctx := context.Background()
			binary, installed, err := claudecode.Installed(ctx, base)
			if err != nil {
				return err
			}
			if !installed {
				fmt.Println("Already logged out.")
				return nil
			}
			cmd := claudecode.Command(ctx, binary, loginDir, "auth", "logout")
			cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("claude: logout: %w", err)
			}
			ui.OK("Logged out. Your own Claude Code is unchanged.")
			return nil
		},
	})

	group := &cobra.Command{
		Use:   "claude",
		Short: "Your own Claude Code, as the platform runs it on this machine",
	}
	group.AddCommand(login, status, logout)
	return group
}

// serverBase is the server this machine belongs to, which names the Claude Code
// build to run and serves it.
func serverBase(cfgPath string) (string, error) {
	cfg, err := config.Load(cfgPath)
	if err != nil || cfg == nil || cfg.Base == "" {
		return "", fmt.Errorf("no server known yet — run `cheesehost link connect <server-url>` first")
	}
	return cfg.Base, nil
}

// modelTokenEnv carries a model service's key to `claude login` from a program
// that runs it, such as the desktop app, so the key is in no command line.
const modelTokenEnv = "CHEESE_MODEL_TOKEN"

// useModelService points this machine's sessions at another model service,
// installing the pinned build first so they can start.
func useModelService(cfgPath, baseURL, model string) error {
	if baseURL == "" || model == "" {
		return errors.New("a model service needs both --base-url and --model")
	}
	token, err := modelToken(os.Stdin)
	if err != nil {
		return err
	}
	service := claudecode.ModelService{BaseURL: baseURL, Token: token, Model: model}
	if err := service.Check(); err != nil {
		return err
	}
	base, err := serverBase(cfgPath)
	if err != nil {
		return err
	}
	ctx := context.Background()
	binary, err := claudecode.Ensure(ctx, base, os.Stdout)
	if err != nil {
		return err
	}
	loginDir, err := claudecode.LoginDir()
	if err != nil {
		return err
	}
	if err := claudecode.SaveModelService(loginDir, service); err != nil {
		return err
	}
	return printClaudeStatus(ctx, binary, loginDir)
}

// modelToken is the model service's key: from the environment when a program
// passes it, else asked for without echo at a terminal, else one line of input.
func modelToken(in *os.File) (string, error) {
	if token := strings.TrimSpace(os.Getenv(modelTokenEnv)); token != "" {
		return token, nil
	}
	if term.IsTerminal(int(in.Fd())) {
		fmt.Fprint(os.Stderr, "Model service key: ")
		secret, err := term.ReadPassword(int(in.Fd()))
		fmt.Fprintln(os.Stderr)
		if err != nil {
			return "", err
		}
		return strings.TrimSpace(string(secret)), nil
	}
	line, err := bufio.NewReader(in).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return "", err
	}
	return strings.TrimSpace(line), nil
}

func printClaudeStatus(ctx context.Context, binary, loginDir string) error {
	service, err := claudecode.LoadModelService(loginDir)
	if err != nil {
		return err
	}
	if service != nil {
		host := service.BaseURL
		if u, err := url.Parse(service.BaseURL); err == nil && u.Host != "" {
			host = u.Host
		}
		ui.OK("Claude Code uses the model service at %s (%s).", host, service.Model)
		return nil
	}
	status, err := claudecode.ReadStatus(ctx, binary, loginDir)
	if err != nil {
		return err
	}
	if !status.LoggedIn {
		ui.Warn("Claude Code is not logged in.")
		ui.Hint("`cheesehost claude login` to log in")
		return nil
	}
	detail := status.AuthMethod
	if status.SubscriptionType != "" {
		detail += " · " + status.SubscriptionType
	}
	ui.OK("Claude Code is logged in (%s).", detail)
	return nil
}
