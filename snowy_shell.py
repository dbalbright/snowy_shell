#!/usr/bin/env python3
"""
snowy_shell.py - Terminal with ASCII snow effect

Makes it snow in ASCII while retaining the prompt and full shell functionality.
Uses the alternate screen buffer so your existing terminal content is preserved
when you exit.

Usage:
    python snowy_shell.py [options] [command]

Options:
    --shell SHELL    Shell to use (default: auto-detect)
    --density N      Snow density: flakes per 100 cells (default: 2.0)
    --speed S        Snow speed multiplier (default: 1.0)
    --chars CHARS    Snowflake characters (default: ASCII set)
    --unicode        Use Unicode snowflake characters
    --no-clear       Don't restore screen on exit (use with caution)
    --no-snow        Start with snow disabled (toggle on later)

Command:
    Optional command to execute in the shell (e.g., echo "hello")
    If provided, the shell runs the command and exits.

Quitting:
    Type 'exit' in the shell, or press Ctrl+C.
    On Unix: send SIGUSR1 to toggle snow, SIGUSR2 to force quit.
    On any platform: create ~/.snowy_shell_exit to force quit,
                     or ~/.snowy_shell_toggle to toggle snow on/off.
"""

import os
import sys
import time
import random
import threading
import subprocess
import argparse
import ctypes
import shutil
import signal
import shlex

# ANSI escape codes
SAVE_CURSOR = '\x1b[s'
RESTORE_CURSOR = '\x1b[u'
CLEAR_SCREEN = '\x1b[2J'
CURSOR_HOME = '\x1b[H'
HIDE_CURSOR = '\x1b[?25l'
SHOW_CURSOR = '\x1b[?25h'

# Alternate screen buffer - preserves existing terminal content
# When we enter, the current screen is saved. When we exit, it's restored.
ALT_SCREEN_ENTER = '\x1b[?1049h'
ALT_SCREEN_EXIT = '\x1b[?1049l'

# Snowflake characters - ASCII by default for maximum compatibility
DEFAULT_SNOWFLAKES = ['*', '+', '.', "'", 'o', 'O']

# Unicode snowflakes for nicer look (Windows Terminal, modern terminals)
UNICODE_SNOWFLAKES = ['*', '+', '.', "'", 'o', 'O',
                      '\u2745', '\u2746', '\u2744']


def read_char_at(x, y):
    """Read the character at terminal position (x, y)."""
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32

            # Try to get the console output handle
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE

            # Check if it's a console handle; if not, open CONOUT$ directly
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                handle = kernel32.CreateFileW(
                    'CONOUT$',
                    0xC0000000,  # GENERIC_READ | GENERIC_WRITE
                    0x3,  # FILE_SHARE_READ | FILE_SHARE_WRITE
                    None,
                    0x3,  # OPEN_EXISTING
                    0,
                    None
                )

            class COORD(ctypes.Structure):
                _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

            char = ctypes.create_unicode_buffer(1)
            length = ctypes.c_ulong(0)
            coord = COORD(X=x, Y=y)
            kernel32.ReadConsoleOutputCharacterW(
                handle, char, 1, coord, ctypes.byref(length))
            return char.value if length.value > 0 else ' '
        except Exception:
            pass
    return ' '


class Terminal:
    """Terminal utilities for size detection and ANSI support."""

    @staticmethod
    def get_size():
        """Get terminal size as (columns, rows)."""
        if os.name == 'nt':
            return Terminal._get_size_windows()
        else:
            try:
                size = os.get_terminal_size()
                return size.columns, size.lines
            except OSError:
                return 80, 24

    @staticmethod
    def _get_size_windows():
        """Get terminal size on Windows using console API."""
        try:
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

            class COORD(ctypes.Structure):
                _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

            class SMALL_RECT(ctypes.Structure):
                _fields_ = [('Left', ctypes.c_short), ('Top', ctypes.c_short),
                            ('Right', ctypes.c_short), ('Bottom', ctypes.c_short)]

            class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
                _fields_ = [('dwSize', COORD), ('dwCursorPosition', COORD),
                            ('wAttributes', ctypes.c_ushort), ('srWindow', SMALL_RECT),
                            ('dwMaximumWindowSize', COORD)]

            csbi = CONSOLE_SCREEN_BUFFER_INFO()
            if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
                width = csbi.srWindow.Right - csbi.srWindow.Left + 1
                height = csbi.srWindow.Bottom - csbi.srWindow.Top + 1
                return width, height
        except Exception:
            pass
        return 80, 24

    @staticmethod
    def enable_ansi():
        """Enable ANSI escape code processing on Windows."""
        if os.name == 'nt':
            try:
                kernel32 = ctypes.windll.kernel32
                STD_OUTPUT_HANDLE = -11
                handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    new_mode = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESS
                    kernel32.SetConsoleMode(handle, new_mode)
                    return True
            except Exception:
                pass
        return False


class Snowflake:
    """A single animated snowflake."""

    def __init__(self, width, height, chars):
        self.width = width
        self.height = height
        self.chars = chars
        self.saved_char = ' '
        self.reset()

    def reset(self):
        """Reset snowflake to a random starting position."""
        self.x = random.randint(0, max(0, self.width - 1))
        self.y = random.uniform(-self.height, 0)
        self.char = random.choice(self.chars)
        self.speed = random.uniform(0.2, 2.0)
        self.drift = random.uniform(-0.5, 0.5)
        self.last_x = None
        self.last_y = None

    def update(self):
        """Update snowflake position. Returns (old_x, old_y, new_x, new_y)."""
        self.last_x = int(self.x)
        self.last_y = int(self.y)
        self.y += self.speed
        self.x += self.drift
        # Clamp x to screen bounds
        if self.x < 0:
            self.x = 0
        elif self.x >= self.width:
            self.x = self.width - 1
        # Reset if fallen off bottom
        if self.y >= self.height:
            self.reset()
        return self.last_x, self.last_y, int(self.x), int(self.y)


class SnowyShell:
    """Main snowy shell application."""

    TOGGLE_FILE = os.path.expanduser('~/.snowy_shell_toggle')
    EXIT_FILE = os.path.expanduser('~/.snowy_shell_exit')

    def __init__(self, shell=None, density=2.0, speed=1.0, chars=None,
                 no_clear=False, unicode=False, no_snow=False, command=None):
        self.shell = shell
        self.density = density
        self.speed = speed
        self.chars = chars or (UNICODE_SNOWFLAKES if unicode else DEFAULT_SNOWFLAKES)
        self.no_clear = no_clear
        self.command = command

        self.running = True
        self.snow_enabled = not no_snow
        self.shell_proc = None
        self.snowflakes = []
        self.width, self.height = Terminal.get_size()
        self.lock = threading.Lock()
        self.pid = os.getpid()

        # Clean up any stale control files
        for f in [self.TOGGLE_FILE, self.EXIT_FILE]:
            if os.path.exists(f):
                os.remove(f)

    def detect_shell(self):
        """Detect the user's preferred shell."""
        if self.shell:
            return self.shell

        if os.name == 'nt':
            # Check SHELL env var first (e.g., Git Bash)
            shell = os.environ.get('SHELL', '')
            if shell and os.path.exists(shell):
                return shell
            # Try common shells in order of preference
            for candidate in ['pwsh.exe', 'powershell.exe', 'cmd.exe']:
                path = shutil.which(candidate)
                if path:
                    return path
            return 'cmd.exe'
        else:
            return os.environ.get('SHELL', '/bin/bash')

    def init_snowflakes(self):
        """Initialize snowflakes based on terminal size and density."""
        if self.width < 10 or self.height < 5:
            return
        count = max(1, int(self.width * self.height * self.density / 100))
        self.snowflakes = [Snowflake(self.width, self.height, self.chars)
                           for _ in range(count)]

    def write_terminal(self, data):
        """Write data directly to terminal (unbuffered, via fd 1)."""
        try:
            os.write(1, data.encode('utf-8', errors='replace'))
        except OSError:
            pass

    def check_control_files(self):
        """Check for file-based toggle/exit commands."""
        if os.path.exists(self.EXIT_FILE):
            os.remove(self.EXIT_FILE)
            self.running = False
            return True

        if os.path.exists(self.TOGGLE_FILE):
            os.remove(self.TOGGLE_FILE)
            self.snow_enabled = not self.snow_enabled
            return True

        return False

    def snow_thread(self):
        """Background thread that animates snowflakes."""
        last_check = 0
        while self.running:
            # Check control files every 200ms
            now = time.time()
            if now - last_check > 0.2:
                if self.check_control_files():
                    if not self.running:
                        break
                last_check = now

            if not self.snow_enabled:
                time.sleep(0.1)
                continue

            # Check for terminal resize
            w, h = Terminal.get_size()
            if w != self.width or h != self.height:
                self.width, self.height = w, h
                self.init_snowflakes()

            # Two-phase update: erase all flakes first, then read+draw
            # This ensures read_char_at sees the original text, not other flakes
            erase_output = []
            new_positions = []
            for flake in self.snowflakes:
                old_x, old_y, new_x, new_y = flake.update()

                # Phase 1: Erase old position by restoring saved character
                if old_y is not None and 0 <= old_y < self.height and 0 <= old_x < self.width:
                    erase_output.append(f'\x1b[{old_y + 1};{old_x + 1}H{flake.saved_char}')

                new_positions.append((flake, new_x, new_y))

            # Write all erasures to terminal
            if erase_output:
                with self.lock:
                    self.write_terminal(SAVE_CURSOR + ''.join(erase_output) + RESTORE_CURSOR)

            # Phase 2: Save chars at new positions and draw flakes
            draw_output = []
            for flake, new_x, new_y in new_positions:
                if 0 <= new_y < self.height and 0 <= new_x < self.width:
                    flake.saved_char = read_char_at(new_x, new_y)
                    draw_output.append(f'\x1b[{new_y + 1};{new_x + 1}H{flake.char}')

            # Write all drawings to terminal
            if draw_output:
                with self.lock:
                    self.write_terminal(SAVE_CURSOR + ''.join(draw_output) + RESTORE_CURSOR)

            time.sleep(0.05 / max(0.1, self.speed))

    def signal_handler(self, signum, frame):
        """Handle signals (SIGINT, SIGTERM, SIGUSR1, SIGUSR2)."""
        if signum == signal.SIGINT or signum == signal.SIGTERM:
            self.running = False
            if self.shell_proc:
                try:
                    self.shell_proc.send_signal(signum)
                except Exception:
                    pass
        elif signum == signal.SIGUSR1:
            # Toggle snow
            self.snow_enabled = not self.snow_enabled
        elif signum == signal.SIGUSR2:
            # Force quit
            self.running = False

    def run(self):
        """Run the snowy shell."""
        # Enable ANSI escape codes
        Terminal.enable_ansi()

        # Initialize snowflakes
        self.init_snowflakes()

        # Set up signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.signal_handler)
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, self.signal_handler)
        if hasattr(signal, 'SIGUSR2'):
            signal.signal(signal.SIGUSR2, self.signal_handler)

        # Enter alternate screen buffer (preserves existing terminal content)
        if not self.no_clear:
            self.write_terminal(ALT_SCREEN_ENTER)

        # Clear screen
        self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}')

        # Start snow thread
        snow_t = threading.Thread(target=self.snow_thread, daemon=True)
        snow_t.start()

        # Start shell
        shell_cmd = self.detect_shell()
        try:
            # Build the full command list
            if self.command:
                # Execute a command in the shell, then exit
                if os.name == 'nt':
                    # Join command parts with spaces (shell already stripped quotes)
                    cmd_str = ' '.join(self.command)
                    if 'powershell' in shell_cmd.lower() or 'pwsh' in shell_cmd.lower():
                        shell_args = [shell_cmd, '-Command', cmd_str]
                    else:
                        shell_args = [shell_cmd, '/c', cmd_str]
                else:
                    # On Unix, use shlex.split
                    cmd_str = ' '.join(self.command)
                    shell_args = shlex.split(f'{shell_cmd} -c "{cmd_str}"')
            else:
                # Parse shell command (handle args like "cmd.exe /c echo hi")
                # On Windows, don't use shlex.split - it strips backslashes from paths
                if isinstance(shell_cmd, str):
                    if os.name == 'nt':
                        # On Windows, pass as string - CreateProcess handles it
                        shell_args = shell_cmd
                    else:
                        shell_args = shlex.split(shell_cmd)
                else:
                    shell_args = list(shell_cmd)

            self.shell_proc = subprocess.Popen(
                shell_args,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
        except Exception as e:
            self.write_terminal(ALT_SCREEN_EXIT)
            sys.stderr.write(f'Failed to start shell "{shell_cmd}": {e}\n')
            self.running = False
            return

        # Wait for shell to exit
        try:
            self.shell_proc.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False

        # Clean up: exit alternate screen buffer (restores existing content)
        if not self.no_clear:
            self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}{ALT_SCREEN_EXIT}')


def main():
    parser = argparse.ArgumentParser(
        description='Terminal with ASCII snow effect while retaining shell functionality.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quitting:
    Type 'exit' in the shell, or press Ctrl+C.
    On Unix: send SIGUSR1 to toggle snow, SIGUSR2 to force quit.
    On any platform: create ~/.snowy_shell_exit to force quit,
                     or ~/.snowy_shell_toggle to toggle snow on/off.

Examples:
    python snowy_shell.py                    # Start with defaults
    python snowy_shell.py --shell bash       # Use a specific shell
    python snowy_shell.py --density 5        # More snow
    python snowy_shell.py --speed 2          # Faster snow
    python snowy_shell.py --unicode          # Use Unicode snowflakes
    python snowy_shell.py --chars ".*+"      # Custom snowflake chars
    python snowy_shell.py --no-snow          # Start with snow off
        """
    )
    parser.add_argument('--shell', '-s',
                        help='Shell to use (default: auto-detect)')
    parser.add_argument('--density', '-d', type=float, default=2.0,
                        help='Snow density: flakes per 100 cells (default: 2.0)')
    parser.add_argument('--speed', type=float, default=1.0,
                        help='Snow speed multiplier (default: 1.0)')
    parser.add_argument('--chars', '-c',
                        help='Snowflake characters (default: ASCII set)')
    parser.add_argument('--unicode', '-u', action='store_true',
                        help='Use Unicode snowflake characters')
    parser.add_argument('--no-clear', action='store_true',
                        help="Don't restore screen on exit (use with caution)")
    parser.add_argument('--no-snow', action='store_true',
                        help='Start with snow disabled (toggle on later)')
    parser.add_argument('command', nargs=argparse.REMAINDER,
                        help='Optional command to run in the shell')

    args = parser.parse_args()

    app = SnowyShell(
        shell=args.shell,
        density=args.density,
        speed=args.speed,
        chars=args.chars,
        no_clear=args.no_clear,
        unicode=args.unicode,
        no_snow=args.no_snow,
        command=args.command if args.command else None,
    )
    app.run()


if __name__ == '__main__':
    main()
