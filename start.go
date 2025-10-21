package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
    "path/filepath"
	"sync"
	"syscall"
	"time"
)

func main() {
    homeDir, err := os.UserHomeDir()
    if err != nil {
        fmt.Fprintf(os.Stderr, "failed to determine home directory: %v\n", err)
        os.Exit(1)
    }
    baseDir := filepath.Join(homeDir, "Desktop", "intervention")
    agentDir := filepath.Join(baseDir, "agent", "experiments")
    uiDir := filepath.Join(baseDir, "ui")

	// Start Agent (python run_robots.py) in its own process group
	agentCmd := exec.Command("python3", "run_robots.py")
    agentCmd.Dir = agentDir
	agentCmd.Stdout = os.Stdout
	agentCmd.Stderr = os.Stderr
	agentCmd.Stdin = os.Stdin
	agentCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := agentCmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "failed to start Agent: %v\n", err)
		os.Exit(1)
	}
	agentPID := agentCmd.Process.Pid
	fmt.Printf("Agent started (pid=%d)\n", agentPID)

	// Start UI (yarn run start) in its own process group
	uiCmd := exec.Command("yarn", "run", "start")
    uiCmd.Dir = uiDir
	uiCmd.Stdout = os.Stdout
	uiCmd.Stderr = os.Stderr
	uiCmd.Stdin = os.Stdin
	uiCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if err := uiCmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "failed to start UI: %v\n", err)
		_ = terminateProcessGroup(agentPID, syscall.SIGTERM)
		time.Sleep(1 * time.Second)
		_ = terminateProcessGroup(agentPID, syscall.SIGKILL)
		os.Exit(1)
	}
	uiPID := uiCmd.Process.Pid
	fmt.Printf("UI started (pid=%d)\n", uiPID)

	// Ensure cleanup on program exit
	var cleanupOnce sync.Once
	cleanup := func(exitOnReturn bool) {
		cleanupOnce.Do(func() {
			fmt.Println("Stopping services...")
			_ = terminateProcessGroup(agentPID, syscall.SIGTERM)
			_ = terminateProcessGroup(uiPID, syscall.SIGTERM)
			time.Sleep(2 * time.Second)
			_ = terminateProcessGroup(agentPID, syscall.SIGKILL)
			_ = terminateProcessGroup(uiPID, syscall.SIGKILL)
			if exitOnReturn {
				// Give a moment for children to reap before exit
				time.Sleep(200 * time.Millisecond)
			}
		})
	}
	defer cleanup(false)

	// Prepare waiters
	agentDone := make(chan error, 1)
	uiDone := make(chan error, 1)
	go func() { agentDone <- agentCmd.Wait() }()
	go func() { uiDone <- uiCmd.Wait() }()

	// Listen for shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP, syscall.SIGQUIT)

	// Exit when one process ends or a signal is received
	exitCode := 0
	var reason string
	select {
	case err := <-agentDone:
		if err != nil {
			exitCode = extractExitCode(err)
		}
		reason = "Agent exited"
		cleanup(true)
	case err := <-uiDone:
		if err != nil {
			exitCode = extractExitCode(err)
		}
		reason = "UI exited"
		cleanup(true)
	case sig := <-sigCh:
		reason = fmt.Sprintf("Received signal %v", sig)
		if sig == os.Interrupt || sig == syscall.SIGINT {
			exitCode = 130
		} else {
			exitCode = 1
		}
		cleanup(true)
	}

	// Drain remaining waiters with a timeout
	waitWithTimeout(agentDone, 5*time.Second)
	waitWithTimeout(uiDone, 5*time.Second)

	fmt.Printf("Exiting: %s (code=%d)\n", reason, exitCode)
	os.Exit(exitCode)
}

func waitWithTimeout(ch <-chan error, timeout time.Duration) {
	select {
	case <-ch:
		return
	case <-time.After(timeout):
		return
	}
}

func terminateProcessGroup(pid int, sig syscall.Signal) error {
	if pid <= 0 {
		return errors.New("invalid pid")
	}
	// Send to process group (negative pid). Fall back to direct PID.
	if err := syscall.Kill(-pid, sig); err == nil {
		return nil
	}
	return syscall.Kill(pid, sig)
}

func extractExitCode(err error) int {
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		if status, ok := exitErr.Sys().(syscall.WaitStatus); ok {
			return status.ExitStatus()
		}
	}
	return 1
}



