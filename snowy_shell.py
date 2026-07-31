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
import struct

# Unix-only imports
if os.name != 'nt':
    import fcntl
    import termios
    import select
    import pty as pty_module

# ANSI escape codes
SAVE_CURSOR = '\x1b[s'
RESTORE_CURSOR = '\x1b[u'
CLEAR_SCREEN = '\x1b[2J'
CURSOR_HOME = '\x1b[H'
HIDE_CURSOR = '\x1b[?25l'
SHOW_CURSOR = '\x1b[?25h'
RESET_ATTRS = '\x1b[0m'

# Alternate screen buffer - preserves existing terminal content
ALT_SCREEN_ENTER = '\x1b[?1049h'
ALT_SCREEN_EXIT = '\x1b[?1049l'

# Snowflake characters - ASCII by default for maximum compatibility
DEFAULT_SNOWFLAKES = ['*', '+', '.', "'", 'o', 'O']

# Unicode snowflakes for nicer look (Windows Terminal, modern terminals)
UNICODE_SNOWFLAKES = ['*', '+', '.', "'", 'o', 'O',
                      '\u2745', '\u2746', '\u2744', '\u2747']


def read_char_at(x, y):
    """Read the character at terminal position (x, y).
    
    On Windows: uses ReadConsoleOutputCharacterW to read from the console.
    On Unix: returns ' ' (actual character tracking is done via ScreenBuffer).
    """
    if os.name == 'nt':
        return _read_char_at_windows(x, y)
    return ' '


def _read_char_at_windows(x, y):
    """Read a character from the Windows console at (x, y)."""
    try:
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            handle = kernel32.CreateFileW(
                'CONOUT$',
                0xC0000000,
                0x3,
                None,
                0x3,
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
                    new_mode = mode.value | 0x0004
                    kernel32.SetConsoleMode(handle, new_mode)
                    return True
            except Exception:
                pass
        return False


class TerminalCell:
    """A single cell in the terminal screen buffer, storing character and ANSI attributes."""

    def __init__(self):
        self.char = ' '
        self.sgr = None  # SGR state dict or None for default

    def set(self, char, sgr):
        """Set the cell content."""
        self.char = char
        self.sgr = dict(sgr) if sgr else None

    def get_sgr_ansi(self):
        """Get the ANSI escape sequence for this cell's SGR attributes."""
        if not self.sgr:
            return RESET_ATTRS
        codes = []
        state = self.sgr
        if state.get('bold'):
            codes.append('1')
        if state.get('italic'):
            codes.append('3')
        if state.get('underline'):
            codes.append('4')
        fg = state.get('fg', '')
        bg = state.get('bg', '')
        if fg:
            codes.append(fg)
        if bg:
            codes.append(bg)
        if codes:
            return f'\x1b[{";".join(codes)}m'
        return RESET_ATTRS


class ScreenBuffer:
    """2D buffer tracking terminal content with ANSI attributes.
    
    Used on Unix systems where there's no Console API equivalent to
    ReadConsoleOutputCharacterW. The buffer is updated by parsing
    ANSI escape codes from the PTY output.
    """

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.cells = [[TerminalCell() for _ in range(width)] for _ in range(height)]
        self.cursor_x = 0
        self.cursor_y = 0
        self.sgr_state = {}  # Current SGR state
        self._reset_sgr()

    def _reset_sgr(self):
        """Reset SGR state to defaults."""
        self.sgr_state = {
            'fg': '',
            'bg': '',
            'bold': False,
            'italic': False,
            'underline': False,
        }

    def get_cell(self, x, y):
        """Get the cell at (x, y). Returns None if out of bounds."""
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.cells[y][x]
        return None

    def write_char(self, char):
        """Write a character at the current cursor position and advance."""
        if 0 <= self.cursor_y < self.height and 0 <= self.cursor_x < self.width:
            cell = self.cells[self.cursor_y][self.cursor_x]
            cell.set(char, self.sgr_state)
            self.cursor_x += 1

    def set_cursor(self, x, y):
        """Set cursor position (0-based)."""
        self.cursor_x = max(0, min(x, self.width - 1))
        self.cursor_y = max(0, min(y, self.height - 1))

    def process_sgr(self, params):
        """Process SGR (Select Graphic Rendition) parameters."""
        if not params:
            self._reset_sgr()
            return

        i = 0
        while i < len(params):
            code = params[i]

            if code == 0:
                self._reset_sgr()
            elif code == 1:
                self.sgr_state['bold'] = True
            elif code == 3:
                self.sgr_state['italic'] = True
            elif code == 4:
                self.sgr_state['underline'] = True
            elif code == 22:
                self.sgr_state['bold'] = False
            elif code == 23:
                self.sgr_state['italic'] = False
            elif code == 24:
                self.sgr_state['underline'] = False
            elif 30 <= code <= 37:
                self.sgr_state['fg'] = str(code)
            elif code == 38:
                # Extended foreground color
                if i + 1 < len(params):
                    color_type = params[i + 1]
                    if color_type == 5 and i + 2 < len(params):
                        # 256-color: 38;5;N
                        self.sgr_state['fg'] = f'38;5;{params[i + 2]}'
                        i += 2
                    elif color_type == 2 and i + 4 < len(params):
                        # Truecolor: 38;2;R;G;B
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        self.sgr_state['fg'] = f'38;2;{r};{g};{b}'
                        i += 4
            elif 40 <= code <= 47:
                self.sgr_state['bg'] = str(code)
            elif code == 39:
                self.sgr_state['fg'] = ''
            elif code == 48:
                # Extended background color
                if i + 1 < len(params):
                    color_type = params[i + 1]
                    if color_type == 5 and i + 2 < len(params):
                        self.sgr_state['bg'] = f'48;5;{params[i + 2]}'
                        i += 2
                    elif color_type == 2 and i + 4 < len(params):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        self.sgr_state['bg'] = f'48;2;{r};{g};{b}'
                        i += 4
            elif code == 49:
                self.sgr_state['bg'] = ''

            i += 1

    def clear_range(self, start_y, start_x, end_y, end_x):
        """Clear a rectangular range of cells."""
        for y in range(max(0, start_y), min(self.height, end_y + 1)):
            for x in range(max(0, start_x), min(self.width, end_x + 1)):
                cell = self.cells[y][x]
                cell.char = ' '
                cell.sgr = None

    def clear_screen(self):
        """Clear the entire screen buffer."""
        for row in self.cells:
            for cell in row:
                cell.char = ' '
                cell.sgr = None

    def clear_line(self):
        """Clear from cursor to end of line."""
        if 0 <= self.cursor_y < self.height:
            for x in range(self.cursor_x, self.width):
                cell = self.cells[self.cursor_y][x]
                cell.char = ' '
                cell.sgr = None

    def resize(self, width, height):
        """Resize the screen buffer."""
        self.width = width
        self.height = height
        self.cells = [[TerminalCell() for _ in range(width)] for _ in range(height)]
        self.cursor_x = 0
        self.cursor_y = 0
        self._reset_sgr()

    def get_attrs_at(self, x, y):
        """Get the SGR attributes at position (x, y) for restoration."""
        cell = self.get_cell(x, y)
        if cell is None:
            return ' ', None
        return cell.char, cell.sgr


class AnsiParser:
    """Simple ANSI escape sequence parser that updates a ScreenBuffer."""

    def __init__(self, screen_buffer):
        self.screen = screen_buffer
        self.buffer = ''

    def feed(self, text):
        """Feed text to the parser."""
        self.buffer += text
        while True:
            # Check for escape sequences
            esc_idx = self.buffer.find('\x1b')
            if esc_idx == -1:
                # No escape sequences, process all text
                self._process_text(self.buffer)
                self.buffer = ''
                break

            # Process text before the escape
            if esc_idx > 0:
                self._process_text(self.buffer[:esc_idx])
                self.buffer = self.buffer[esc_idx:]

            # Check for ESC [
            if len(self.buffer) >= 2 and self.buffer[1] == '[':
                self._parse_csi()
            elif len(self.buffer) >= 2 and self.buffer[1] == ']':
                self._parse_osc()
            else:
                # Simple ESC sequence (like ESC c for reset)
                if len(self.buffer) >= 2:
                    if self.buffer[1] == 'c':
                        # Full reset
                        self.screen.clear_screen()
                        self.screen.cursor_x = 0
                        self.screen.cursor_y = 0
                        self.screen._reset_sgr()
                    self.buffer = self.buffer[2:]
                else:
                    break

    def _parse_csi(self):
        """Parse a CSI (Control Sequence Introducer) sequence."""
        # Find the end of the CSI sequence (letter terminator)
        i = 2
        while i < len(self.buffer) and self.buffer[i] not in '@$%&*^/_':
            # Actually, CSI ends with a final byte in range 0x40-0x7E
            if 0x40 <= ord(self.buffer[i]) <= 0x7e:
                break
            i += 1

        if i >= len(self.buffer):
            return  # Incomplete, wait for more data

        params = self.buffer[2:i]
        command = self.buffer[i]

        self.buffer = self.buffer[i + 1:]

        # Parse parameters
        param_list = []
        if params:
            parts = params.split(';')
            for part in parts:
                part = part.strip()
                if part.isdigit():
                    param_list.append(int(part))

        self._handle_csi(command, param_list)

    def _parse_osc(self):
        """Parse an OSC (Operating System Command) sequence.
        
        We skip OSC sequences (they're for window titles, etc.).
        """
        # OSC sequences end with BEL (\x07) or ST (ESC \)
        bel_idx = self.buffer.find('\x07', 2)
        st_idx = self.buffer.find('\x1b\\', 2)

        if bel_idx != -1 and (st_idx == -1 or bel_idx < st_idx):
            self.buffer = self.buffer[bel_idx + 1:]
        elif st_idx != -1:
            self.buffer = self.buffer[st_idx + 2:]
        # Otherwise, leave it in buffer (incomplete)

    def _handle_csi(self, command, params):
        """Handle a CSI command."""
        if command == 'H' or command == 'f':
            # Cursor Position: ESC[row;col H
            row = params[0] - 1 if params else 0
            col = params[1] - 1 if len(params) > 1 else 0
            self.screen.set_cursor(col, row)

        elif command == 'A':
            # Cursor Up
            count = params[0] if params else 1
            self.screen.cursor_y = max(0, self.screen.cursor_y - count)

        elif command == 'B':
            # Cursor Down
            count = params[0] if params else 1
            self.screen.cursor_y = min(self.screen.height - 1, self.screen.cursor_y + count)

        elif command == 'C':
            # Cursor Forward
            count = params[0] if params else 1
            self.screen.cursor_x = min(self.screen.width - 1, self.screen.cursor_x + count)

        elif command == 'D':
            # Cursor Back
            count = params[0] if params else 1
            self.screen.cursor_x = max(0, self.screen.cursor_x - count)

        elif command == 'J':
            # Erase Display
            mode = params[0] if params else 0
            if mode == 0:
                # Clear from cursor to end of screen
                self.screen.clear_range(
                    self.screen.cursor_y, self.screen.cursor_x,
                    self.screen.height - 1, self.screen.width - 1
                )
            elif mode == 1:
                # Clear from start to cursor
                self.screen.clear_range(0, 0, self.screen.cursor_y, self.screen.cursor_x)
            elif mode == 2:
                # Clear entire screen
                self.screen.clear_screen()

        elif command == 'K':
            # Erase in Line
            mode = params[0] if params else 0
            if mode == 0:
                # Clear from cursor to end of line
                self.screen.clear_line()
            elif mode == 2:
                # Clear entire line
                if 0 <= self.screen.cursor_y < self.screen.height:
                    for x in range(self.screen.width):
                        cell = self.screen.cells[self.screen.cursor_y][x]
                        cell.char = ' '
                        cell.sgr = None

        elif command == 'm':
            # SGR: Select Graphic Rendition
            self.screen.process_sgr(param_list if params else [0])

        elif command == 'h' or command == 'l':
            # Set/Reset mode - we mostly ignore these for screen buffer purposes
            pass

        elif command == 'r':
            # Set scrolling region - we ignore this for now
            pass

    def _process_text(self, text):
        """Process regular text characters."""
        for char in text:
            if char == '\n':
                self.screen.cursor_y += 1
                self.screen.cursor_x = 0
            elif char == '\r':
                self.screen.cursor_x = 0
            elif char == '\t':
                self.screen.cursor_x = ((self.screen.cursor_x // 8) + 1) * 8
                if self.screen.cursor_x >= self.screen.width:
                    self.screen.cursor_x = self.screen.width - 1
            elif char == '\x08':
                # Backspace
                self.screen.cursor_x = max(0, self.screen.cursor_x - 1)
            else:
                self.screen.write_char(char)
                # Handle line wrapping
                if self.screen.cursor_x >= self.screen.width:
                    self.screen.cursor_x = 0
                    self.screen.cursor_y += 1


class Snowflake:
    """A single animated snowflake."""

    def __init__(self, width, height, chars):
        self.width = width
        self.height = height
        self.chars = chars
        self.saved_char = ' '
        self.saved_sgr = None
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
        if self.x < 0:
            self.x = 0
        elif self.x >= self.width:
            self.x = self.width - 1
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
        self.buffer_lock = threading.Lock()
        self.pid = os.getpid()

        # Unix-specific: screen buffer and PTY
        self.screen_buffer = None
        self.ansi_parser = None
        self.master_fd = None
        self.pty_proc = None
        self.use_pty = os.name != 'nt'

        if self.use_pty:
            self.screen_buffer = ScreenBuffer(self.width, self.height)
            self.ansi_parser = AnsiParser(self.screen_buffer)

        # Clean up any stale control files
        for f in [self.TOGGLE_FILE, self.EXIT_FILE]:
            if os.path.exists(f):
                os.remove(f)

    def detect_shell(self):
        """Detect the user's preferred shell."""
        if self.shell:
            return self.shell

        if os.name == 'nt':
            shell = os.environ.get('SHELL', '')
            if shell and os.path.exists(shell):
                return shell
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
                if self.screen_buffer:
                    self.screen_buffer.resize(w, h)
                if self.master_fd is not None and self.pty_proc:
                    self._update_pty_size()

            # Two-phase update: erase all flakes first, then read+draw
            erase_output = []
            new_positions = []
            for flake in self.snowflakes:
                old_x, old_y, new_x, new_y = flake.update()

                # Phase 1: Erase old position by restoring saved character
                if old_y is not None and 0 <= old_y < self.height and 0 <= old_x < self.width:
                    if self.use_pty and self.screen_buffer:
                        cell = self.screen_buffer.get_cell(old_x, old_y)
                        if cell:
                            sgr_ansi = cell.get_sgr_ansi()
                            erase_output.append(f'\x1b[{old_y + 1};{old_x + 1}H{sgr_ansi}{flake.saved_char}{RESET_ATTRS}')
                        else:
                            erase_output.append(f'\x1b[{old_y + 1};{old_x + 1}H{flake.saved_char}')
                    else:
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
                    if self.use_pty and self.screen_buffer:
                        with self.buffer_lock:
                            char, sgr = self.screen_buffer.get_attrs_at(new_x, new_y)
                        flake.saved_char = char
                        flake.saved_sgr = sgr
                    else:
                        flake.saved_char = read_char_at(new_x, new_y)
                        flake.saved_sgr = None
                    draw_output.append(f'\x1b[{new_y + 1};{new_x + 1}H{flake.char}')

            # Write all drawings to terminal
            if draw_output:
                with self.lock:
                    self.write_terminal(SAVE_CURSOR + ''.join(draw_output) + RESTORE_CURSOR)

            time.sleep(0.05 / max(0.1, self.speed))

    def _update_pty_size(self):
        """Update the PTY window size to match the terminal."""
        if self.master_fd is not None:
            try:
                winsize = struct.pack('HHHH', self.height, self.width, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    def _output_reader(self):
        """Read from PTY master, parse ANSI, update screen buffer, forward to terminal."""
        while self.running:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if self.master_fd in r:
                    data = os.read(self.master_fd, 4096)
                    if not data:
                        break
                    text = data.decode('utf-8', errors='replace')

                    # Parse ANSI and update screen buffer
                    if self.ansi_parser:
                        with self.buffer_lock:
                            self.ansi_parser.feed(text)

                    # Forward to real terminal
                    with self.lock:
                        self.write_terminal(text)
            except OSError:
                break
            except Exception:
                break

    def _input_forwarder(self):
        """Forward real terminal input to PTY master."""
        while self.running:
            try:
                r, _, _ = select.select([sys.stdin.fileno()], [], [], 0.1)
                if sys.stdin.fileno() in r:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data:
                        break
                    os.write(self.master_fd, data)
            except OSError:
                break
            except Exception:
                break

    def signal_handler(self, signum, frame):
        """Handle signals (SIGINT, SIGTERM, SIGUSR1, SIGUSR2)."""
        if signum == signal.SIGINT or signum == signal.SIGTERM:
            self.running = False
            if self.shell_proc:
                try:
                    self.shell_proc.send_signal(signum)
                except Exception:
                    pass
            elif self.pty_proc:
                try:
                    self.pty_proc.send_signal(signum)
                except Exception:
                    pass
        elif signum == signal.SIGUSR1:
            self.snow_enabled = not self.snow_enabled
        elif signum == signal.SIGUSR2:
            self.running = False

    def _setup_sigwinch(self):
        """Set up SIGWINCH handler for terminal resize."""
        if hasattr(signal, 'SIGWINCH') and os.name != 'nt':
            signal.signal(signal.SIGWINCH, self._sigwinch_handler)

    def _sigwinch_handler(self, signum, frame):
        """Handle terminal resize."""
        w, h = Terminal.get_size()
        if w != self.width or h != self.height:
            self.width, self.height = w, h
            self.init_snowflakes()
            if self.screen_buffer:
                self.screen_buffer.resize(w, h)
            if self.master_fd is not None:
                self._update_pty_size()

    def run_without_pty(self):
        """Run the snowy shell without PTY (Windows or fallback)."""
        # Enter alternate screen buffer
        if not self.no_clear:
            self.write_terminal(ALT_SCREEN_ENTER)

        self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}')

        snow_t = threading.Thread(target=self.snow_thread, daemon=True)
        snow_t.start()

        shell_cmd = self.detect_shell()
        try:
            if self.command:
                if os.name == 'nt':
                    cmd_str = ' '.join(self.command)
                    if 'powershell' in shell_cmd.lower() or 'pwsh' in shell_cmd.lower():
                        shell_args = [shell_cmd, '-Command', cmd_str]
                    else:
                        shell_args = [shell_cmd, '/c', cmd_str]
                else:
                    cmd_str = ' '.join(self.command)
                    shell_args = shlex.split(f'{shell_cmd} -c "{cmd_str}"')
            else:
                if isinstance(shell_cmd, str):
                    if os.name == 'nt':
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

        try:
            self.shell_proc.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False

        if not self.no_clear:
            self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}{ALT_SCREEN_EXIT}')

    def run_with_pty(self):
        """Run the snowy shell with PTY (Unix)."""
        # Enter alternate screen buffer
        if not self.no_clear:
            self.write_terminal(ALT_SCREEN_ENTER)

        self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}')

        # Set up SIGWINCH for terminal resize
        self._setup_sigwinch()

        # Create PTY pair
        master_fd, slave_fd = pty_module.openpty()
        self.master_fd = master_fd

        # Set initial window size
        self._update_pty_size()

        # Set terminal attributes for the slave
        try:
            attrs = termios.tcgetattr(slave_fd)
            attrs[3] = attrs[3] & ~termios.ECHO & ~termios.ICANON
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except Exception:
            pass

        # Spawn shell
        shell_cmd = self.detect_shell()
        try:
            if self.command:
                cmd_str = ' '.join(self.command)
                shell_args = shlex.split(f'{shell_cmd} -c "{cmd_str}"')
            else:
                shell_args = shlex.split(shell_cmd)

            self.pty_proc = subprocess.Popen(
                shell_args,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            self.write_terminal(ALT_SCREEN_EXIT)
            sys.stderr.write(f'Failed to start shell "{shell_cmd}": {e}\n')
            self.running = False
            return

        # Close slave in parent
        os.close(slave_fd)

        # Start output reader thread
        output_t = threading.Thread(target=self._output_reader, daemon=True)
        output_t.start()

        # Start input forwarder thread
        input_t = threading.Thread(target=self._input_forwarder, daemon=True)
        input_t.start()

        # Start snow thread
        snow_t = threading.Thread(target=self.snow_thread, daemon=True)
        snow_t.start()

        # Wait for shell to exit
        try:
            self.pty_proc.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False

        # Clean up
        try:
            os.close(master_fd)
        except Exception:
            pass

        if not self.no_clear:
            self.write_terminal(f'{CLEAR_SCREEN}{CURSOR_HOME}{ALT_SCREEN_EXIT}')

    def run(self):
        """Run the snowy shell."""
        Terminal.enable_ansi()
        self.init_snowflakes()

        signal.signal(signal.SIGINT, self.signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.signal_handler)
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, self.signal_handler)
        if hasattr(signal, 'SIGUSR2'):
            signal.signal(signal.SIGUSR2, self.signal_handler)

        if self.use_pty:
            self.run_with_pty()
        else:
            self.run_without_pty()


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
