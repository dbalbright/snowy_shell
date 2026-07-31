#!/usr/bin/env python3
"""Integration test for snowy_shell.py - runs the program with a short-lived shell."""

import subprocess
import sys
import time

def test_snowy_shell_integration():
    """Test that snowy_shell.py works end-to-end."""
    # Use PowerShell which handles non-tty stdin better than cmd.exe
    proc = subprocess.Popen(
        [sys.executable, r'C:\Users\danie\prj\snow\snowy_shell.py',
         '--shell', 'powershell.exe -Command "Write-Host Hello from snowy shell; Start-Sleep -Seconds 3; Write-Host Goodbye"'],
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
    success = test_snowy_shell_integration()
    sys.exit(0 if success else 1)
