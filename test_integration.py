#!/usr/bin/env python3
"""Integration tests for snowy_shell.py."""

import os
import pty
import re
import select
import subprocess
import sys
import time

from snowy_shell import AnsiParser, ScreenBuffer


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

def test_scrolling_output_preserves_text():
    """Verify snow does not become embedded when command output scrolls."""
    if os.name == 'nt':
        return True

    command = (
        "sleep 0.2; i=1; while [ $i -le 40 ]; do "
        "printf 'ROW%02d preserved text\\n' $i; "
        "sleep 0.01; i=$((i + 1)); done"
    )
    proc = subprocess.run(
        [
            sys.executable, 'snowy_shell.py', '--no-clear',
            '--shell', '/bin/sh', '--density', '10', '--chars', '*', command,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode('utf-8', errors='replace')

    screen = ScreenBuffer(80, 24)
    AnsiParser(screen).feed(proc.stdout.decode('utf-8', errors='replace'))
    lines = [''.join(cell.char for cell in row).rstrip() for row in screen.cells]
    expected = [f'ROW{i:02d} preserved text' for i in range(20, 41)] + ['', '', '']
    assert lines == expected, '\n'.join(lines)
    print('Scrolling output text preservation test passed')
    return True

def test_child_pty_excludes_ground_rows():
    """Verify the Unix child sees a terminal two rows shorter."""
    if os.name == 'nt':
        return True
    proc = subprocess.run(
        [
            sys.executable, 'snowy_shell.py', '--no-clear', '--no-snow',
            '--shell', '/bin/sh', 'stty size',
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert '22 80' in proc.stdout, repr(proc.stdout)
    assert '\x1b[1;22r' in proc.stdout
    assert '\x1b[r' in proc.stdout
    print('Child PTY ground reservation test passed')
    return True

def test_snow_accumulates_in_reserved_rows():
    """Verify live Unix snow reaches and fills both protected rows."""
    if os.name == 'nt':
        return True
    proc = subprocess.run(
        [
            sys.executable, 'snowy_shell.py', '--no-clear',
            '--shell', '/bin/sh', '--density', '10', '--speed', '4',
            '--chars', '*', 'sleep 0.8',
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert re.search(r'\x1b\[23;\d+H\*', proc.stdout)
    assert re.search(r'\x1b\[24;\d+H\*', proc.stdout)
    print('Live Unix ground accumulation test passed')
    return True

def test_scrolling_output_stays_above_live_ground():
    """Verify active shell output and accumulated snow occupy separate rows."""
    if os.name == 'nt':
        return True
    command = (
        "sleep 0.4; i=1; while [ $i -le 35 ]; do "
        "printf 'SAFE_ROW_%02d\\n' $i; i=$((i + 1)); done; sleep 0.5"
    )
    proc = subprocess.run(
        [
            sys.executable, 'snowy_shell.py', '--no-clear',
            '--shell', '/bin/sh', '--density', '10', '--speed', '4',
            '--chars', '*', command,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert re.search(r'\x1b\[23;\d+H\*', proc.stdout)
    assert re.search(r'\x1b\[24;\d+H\*', proc.stdout)

    # After the output has scrolled into its final layout, every non-snow
    # overlay write must restore the exact character at that shell cell.
    marker = proc.stdout.find('SAFE_ROW_35') + len('SAFE_ROW_35')
    expected = {}
    for row, number in enumerate(range(15, 36), 1):
        for column, char in enumerate(f'SAFE_ROW_{number:02d}', 1):
            expected[(row, column)] = char
    writes = re.finditer(
        r'\x1b\[(\d+);(\d+)H(?:\x1b\[[0-9;]*m)?([^\x1b])',
        proc.stdout[marker:],
    )
    for match in writes:
        row, column = int(match.group(1)), int(match.group(2))
        char = match.group(3)
        if row <= 22 and char != '*':
            assert char == expected.get((row, column), ' ')
    print('Scrolling output and live ground isolation test passed')
    return True

def test_repeated_zsh_listings_preserve_text():
    """Verify zsh prompt cleanup and repeated listings stay synchronized."""
    if os.name == 'nt' or not os.path.exists('/bin/zsh'):
        return True

    command = (
        "sleep 0.2; ls -lah; sleep 0.2; "
        "ls -lah; sleep 0.2; ls -lah; sleep 0.2"
    )
    proc = subprocess.run(
        [
            sys.executable, 'snowy_shell.py', '--no-clear',
            '--shell', '/bin/zsh -f', '--density', '6', command,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode('utf-8', errors='replace')

    actual = ScreenBuffer(80, 24)
    AnsiParser(actual).feed(proc.stdout.decode('utf-8', errors='replace'))

    expected_proc = subprocess.run(
        ['/bin/zsh', '-f', '-c', command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    expected = ScreenBuffer(80, 24)
    expected_text = expected_proc.stdout.decode('utf-8', errors='replace')
    AnsiParser(expected).feed(
        '\x1b[1;22r' + expected_text.replace('\n', '\r\n')
    )

    actual_lines = [''.join(c.char for c in row) for row in actual.cells]
    expected_lines = [''.join(c.char for c in row) for row in expected.cells]
    assert actual_lines == expected_lines
    print('Repeated zsh listing preservation test passed')
    return True

if __name__ == '__main__':
    success = test_unix_interactive_shell()
    if os.name != 'nt' and os.path.exists('/bin/zsh'):
        success = test_unix_interactive_shell('/bin/zsh -f') and success
    success = test_snowy_shell_integration() and success
    success = test_scrolling_output_preserves_text() and success
    success = test_child_pty_excludes_ground_rows() and success
    success = test_snow_accumulates_in_reserved_rows() and success
    success = test_scrolling_output_stays_above_live_ground() and success
    success = test_repeated_zsh_listings_preserve_text() and success
    sys.exit(0 if success else 1)
