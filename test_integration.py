#!/usr/bin/env python3
"""Integration tests for snowy_shell.py."""

import os
import pty
import select
import subprocess
import sys
import time


def test_unix_interactive_shell(shell='/bin/sh'):
    """Verify a Unix shell gets a controlling terminal and relays input/output."""
    if os.name == 'nt':
        return True

    pid, outer_fd = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env['PS1'] = 'SNOWY_PROMPT> '
        args = [sys.executable, 'snowy_shell.py', '--no-clear', '--shell', shell]
        os.execve(sys.executable, args, env)

    output = b''
    deadline = time.monotonic() + 10
    sent_command = False
    sent_exit = False
    status = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([outer_fd], [], [], 0.1)
            if outer_fd in readable:
                try:
                    chunk = os.read(outer_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk

            if b'SNOWY_PROMPT> ' in output and not sent_command:
                os.write(outer_fd, b"printf 'RESULT_%s\\n' OK\r")
                sent_command = True
            if b'RESULT_OK' in output and sent_command and not sent_exit:
                os.write(outer_fd, b'exit\r')
                sent_exit = True

            waited_pid, status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                break
        else:
            os.kill(pid, 9)

        if status is None:
            _, status = os.waitpid(pid, 0)
    finally:
        os.close(outer_fd)

    decoded = output.decode('utf-8', errors='replace')
    assert sent_command, f'interactive prompt was not displayed: {decoded!r}'
    assert sent_exit, f'command output was not displayed: {decoded!r}'
    assert os.waitstatus_to_exitcode(status) == 0
    print(f'Unix interactive PTY test passed for {shell}')
    return True

def test_snowy_shell_integration():
    """Test that snowy_shell.py works end-to-end."""
    if os.name == 'nt':
        command = 'echo "Hello from snowy shell"; echo "Goodbye"'
        extra_args = []
    else:
        command = 'printf "Hello from snowy shell\\n"; printf "Goodbye\\n"'
        extra_args = ['--shell', '/bin/sh']

    proc = subprocess.Popen(
        [sys.executable, 'snowy_shell.py', *extra_args, command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    try:
        stdout, stderr = proc.communicate(timeout=15)
        print(f'Return code: {proc.returncode}')
        print(f'Stdout length: {len(stdout)} chars')
        if stderr:
            print(f'Stderr: {repr(stderr[:300])}')

        # Check for key escape sequences
        has_alt_screen = '\x1b[?1049h' in stdout
        has_alt_exit = '\x1b[?1049l' in stdout
        has_clear = '\x1b[2J' in stdout

        # Check for any snowflake character (from DEFAULT_SNOWFLAKES)
        snowflake_chars = ['*', '+', '.', "'", 'o', 'O']
        has_snowflake = any(c in stdout for c in snowflake_chars)
        has_shell_output = 'Hello' in stdout and 'Goodbye' in stdout

        print(f'Has alt screen enter: {has_alt_screen}')
        print(f'Has alt screen exit: {has_alt_exit}')
        print(f'Has clear screen: {has_clear}')
        print(f'Has snowflake chars: {has_snowflake}')
        print(f'Has shell output: {has_shell_output}')

        # Show output snippets
        print(f'\nOutput snippet (first 400 chars):')
        print(repr(stdout[:400]))
        print(f'\nOutput snippet (last 200 chars):')
        print(repr(stdout[-200:]))

        # Assertions
        assert proc.returncode == 0, f"Should exit cleanly (got {proc.returncode})"
        assert has_alt_screen, "Should enter alternate screen buffer"
        assert has_alt_exit, "Should exit alternate screen buffer"
        assert has_clear, "Should clear screen"
        assert has_snowflake, "Should draw snowflakes"
        assert has_shell_output, "Should have shell output"

        print('\nALL INTEGRATION TESTS PASSED!')
        return True

    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        print(f'TIMEOUT - return code: {proc.returncode}')
        print(f'Stdout: {repr(stdout[:400])}')
        if stderr:
            print(f'Stderr: {repr(stderr[:300])}')
        return False

if __name__ == '__main__':
    success = test_unix_interactive_shell()
    if os.name != 'nt' and os.path.exists('/bin/zsh'):
        success = test_unix_interactive_shell('/bin/zsh -f') and success
    success = test_snowy_shell_integration() and success
    sys.exit(0 if success else 1)
