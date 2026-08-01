#!/usr/bin/env python3
"""Test script to verify snowy_shell.py core functionality."""

import os
import sys
import signal

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snowy_shell import (
    Terminal, Snowflake, SnowyShell, read_char_at, sgr_dict_to_ansi,
    TerminalCell, ScreenBuffer, AnsiParser, PtyOutputFilter,
    SAVE_CURSOR, RESTORE_CURSOR, CLEAR_SCREEN, CURSOR_HOME,
    DEC_SAVE_CURSOR, DEC_RESTORE_CURSOR,
    HIDE_CURSOR, SHOW_CURSOR, ALT_SCREEN_ENTER, ALT_SCREEN_EXIT,
    DEFAULT_SNOWFLAKES, UNICODE_SNOWFLAKES,
)

def test_terminal_size():
    """Test terminal size detection."""
    w, h = Terminal.get_size()
    print(f"Terminal size: {w}x{h}")
    assert w > 0, "Width should be positive"
    assert h > 0, "Height should be positive"
    print("  PASS: terminal size detection works")

def test_ansi_enable():
    """Test ANSI enable function."""
    result = Terminal.enable_ansi()
    print(f"ANSI enable result: {result}")
    print("  PASS: ANSI enable function runs without error")

def test_snowflake():
    """Test snowflake creation and update."""
    sf = Snowflake(80, 24, ['*', '+', '.', 'o'])
    assert sf.char in ['*', '+', '.', 'o'], "Char should be from the set"
    assert 0 <= sf.x < 80, "X should be within bounds"
    assert -48 <= sf.y <= 0, "Y should start above screen"
    assert 0.2 <= sf.speed <= 2.0, "Speed should be in range"
    
    old_x, old_y, new_x, new_y = sf.update()
    assert sf.last_x == old_x, "Last X should match old X"
    assert sf.last_y == old_y, "Last Y should match old Y"
    print(f"  PASS: snowflake update works (old: ({old_x},{old_y}) -> new: ({new_x},{new_y}))")
    
    # Test falling off bottom
    sf.y = 100
    sf.update()
    assert sf.y < 24, "Snowflake should reset when falling off bottom"
    print(f"  PASS: snowflake resets when falling off bottom")

def test_snowflake_drift():
    """Test snowflake drift (left/right movement)."""
    sf = Snowflake(80, 24, ['*'])
    sf.drift = 0.5
    sf.x = 40
    sf.y = 0
    initial_x = sf.x
    for _ in range(10):
        sf.update()
    assert sf.x > initial_x, "Snowflake should drift to the right"
    print(f"  PASS: snowflake drift works")

def test_shell_detection():
    """Test shell detection."""
    app = SnowyShell()
    shell = app.detect_shell()
    print(f"Detected shell: {shell}")
    assert shell is not None, "Shell should be detected"
    print("  PASS: shell detection works")

def test_unix_shell_args():
    """Test Unix shell and command arguments preserve their boundaries."""
    args = SnowyShell._unix_shell_args('/bin/zsh -f', ['printf "hello world\\n"'])
    assert args == ['/bin/zsh', '-f', '-c', 'printf "hello world\\n"']
    print("  PASS: Unix shell argument construction works")

def test_snowflake_init():
    """Test snowflake initialization."""
    app = SnowyShell(density=2.0)
    app.width, app.height = 80, 24
    app.init_snowflakes()
    count = len(app.snowflakes)
    expected = max(1, int(80 * 24 * 2.0 / 100))
    print(f"Snowflake count: {count} (expected ~{expected})")
    assert count == expected, f"Expected {expected} snowflakes, got {count}"
    print("  PASS: snowflake initialization works")

def test_write_terminal():
    """Test terminal writing (ANSI escape codes)."""
    app = SnowyShell()
    # Test save/restore cursor
    app.write_terminal(f'{SAVE_CURSOR}\x1b[5;10H*\x1b[u')
    print("  PASS: terminal write works")

def test_batch_update():
    """Test batch snowflake update."""
    app = SnowyShell(density=2.0, speed=1.0)
    app.width, app.height = 80, 24
    app.init_snowflakes()
    
    # Simulate one frame of updates
    output = []
    for flake in app.snowflakes:
        old_x, old_y, new_x, new_y = flake.update()
        if old_y is not None and 0 <= old_y < 24 and 0 <= old_x < 80:
            output.append(f'\x1b[{old_y + 1};{old_x + 1}H ')
        if 0 <= new_y < 24 and 0 <= new_x < 80:
            output.append(f'\x1b[{new_y + 1};{new_x + 1}H{flake.char}')
    
    print(f"  Generated {len(output)} draw commands for {len(app.snowflakes)} snowflakes")
    assert len(output) > 0, "Should generate draw commands"
    print("  PASS: batch update works")

def test_config_options():
    """Test configuration options."""
    # Test custom chars
    app = SnowyShell(chars=['.', 'o', 'O'])
    assert app.chars == ['.', 'o', 'O'], "Custom chars should be used"
    print("  PASS: custom chars option works")
    
    # Test density
    app = SnowyShell(density=5.0)
    app.width, app.height = 80, 24
    app.init_snowflakes()
    expected = max(1, int(80 * 24 * 5.0 / 100))
    assert len(app.snowflakes) == expected, f"Density 5.0 should give {expected} flakes"
    print(f"  PASS: density option works ({len(app.snowflakes)} flakes)")
    
    # Test speed
    app = SnowyShell(speed=2.0)
    assert app.speed == 2.0, "Speed should be 2.0"
    print("  PASS: speed option works")
    
    # Test unicode
    app = SnowyShell(unicode=True)
    assert app.chars == UNICODE_SNOWFLAKES, "Unicode chars should be used"
    print("  PASS: unicode option works")

def test_alt_screen():
    """Test alternate screen buffer escape sequences."""
    assert ALT_SCREEN_ENTER == '\x1b[?1049h', "Alt screen enter should be correct"
    assert ALT_SCREEN_EXIT == '\x1b[?1049l', "Alt screen exit should be correct"
    print("  PASS: alternate screen buffer escape codes are correct")

def test_control_files():
    """Test file-based control (toggle/exit)."""
    app = SnowyShell()
    
    # Test toggle file
    app.snow_enabled = True
    with open(app.TOGGLE_FILE, 'w') as f:
        f.write('')
    app.check_control_files()
    assert not app.snow_enabled, "Snow should be toggled off"
    assert not os.path.exists(app.TOGGLE_FILE), "Toggle file should be deleted"
    print("  PASS: toggle file works")
    
    # Test exit file
    with open(app.EXIT_FILE, 'w') as f:
        f.write('')
    result = app.check_control_files()
    assert not app.running, "Running should be set to False"
    assert result, "Should return True when exit file is detected"
    assert not os.path.exists(app.EXIT_FILE), "Exit file should be deleted"
    print("  PASS: exit file works")

def test_signal_handlers():
    """Test signal handler setup."""
    app = SnowyShell()
    # Test SIGINT
    app.running = True
    app.signal_handler(signal.SIGINT, None)
    assert not app.running, "Running should be False after SIGINT"
    print("  PASS: SIGINT handler works")
    
    # Test SIGUSR1 (toggle)
    app2 = SnowyShell()
    if hasattr(signal, 'SIGUSR1'):
        app2.snow_enabled = True
        app2.signal_handler(signal.SIGUSR1, None)
        assert not app2.snow_enabled, "Snow should be toggled off by SIGUSR1"
        print("  PASS: SIGUSR1 toggle works")
    
    # Test SIGUSR2 (force quit)
    app3 = SnowyShell()
    if hasattr(signal, 'SIGUSR2'):
        app3.running = True
        app3.signal_handler(signal.SIGUSR2, None)
        assert not app3.running, "Running should be False after SIGUSR2"
        print("  PASS: SIGUSR2 force quit works")

def test_snowflake_update_with_bounds():
    """Test snowflake update with various positions."""
    sf = Snowflake(80, 24, ['*'])
    sf.speed = 1.5
    sf.drift = 0
    initial_y = sf.y
    for _ in range(10):
        sf.update()
    assert sf.y > initial_y, f"Snowflake should fall (y: {initial_y} -> {sf.y})"
    print(f"  PASS: snowflake bounds handling works (y: {initial_y:.1f} -> {sf.y:.1f})")

def test_no_snow_option():
    """Test --no-snow option."""
    app = SnowyShell(no_snow=True)
    assert not app.snow_enabled, "Snow should be disabled with --no-snow"
    print("  PASS: --no-snow option works")

def test_unix_reserves_two_ground_rows():
    """Test Unix shell geometry excludes the physical ground rows."""
    app = SnowyShell()
    if app.use_pty and app.height >= 7:
        assert app.ground_rows == 2
        assert app.shell_height == app.height - 2
        assert app.screen_buffer.height == app.shell_height
    print("  PASS: Unix reserves two ground rows")

def test_pid_tracking():
    """Test PID tracking for signal-based control."""
    app = SnowyShell()
    assert app.pid == os.getpid(), "PID should match current process"
    print(f"  PASS: PID tracking works (PID={app.pid})")

def test_read_char_at():
    """Test read_char_at function exists and returns a string."""
    char = read_char_at(0, 0)
    assert isinstance(char, str), "read_char_at should return a string"
    assert len(char) == 1, "read_char_at should return a single character"
    print("  PASS: read_char_at works")

def test_snowflake_saved_char():
    """Test that snowflake has saved_char attribute."""
    sf = Snowflake(80, 24, ['*'])
    assert hasattr(sf, 'saved_char'), "Snowflake should have saved_char attribute"
    assert sf.saved_char == ' ', "saved_char should default to space"
    print("  PASS: snowflake saved_char works")

def test_snow_thread_saves_chars():
    """Test that snow thread saves and restores characters."""
    app = SnowyShell(density=1.0)
    app.init_snowflakes()
    # Each flake should have a saved_char
    for flake in app.snowflakes:
        assert hasattr(flake, 'saved_char'), "Flake should have saved_char"
    print("  PASS: snow thread saves characters")

def test_pty_output_erases_snow_before_forwarding():
    """Test snow is removed before shell output can scroll the terminal."""
    app = SnowyShell(density=1.0)
    app.screen_buffer.set_cursor(2, 1)
    app.screen_buffer.process_sgr([31])
    app.screen_buffer.write_char('X')
    app.drawn_snow = {(2, 1): (' ', None)}
    output = []
    app.write_terminal = output.append

    app._forward_pty_output('\r\nnew output')

    assert '\x1b[2;3H\x1b[31mX\x1b[0m' in output[0]
    assert output[1] == '\r\nnew output'
    assert app.drawn_snow == {}
    print("  PASS: PTY output erases snow before forwarding")

def test_overlapping_snow_restores_cell_once():
    """Test overlapping flakes preserve one canonical underlying cell."""
    app = SnowyShell(density=1.0)
    app.screen_buffer.set_cursor(4, 3)
    app.screen_buffer.write_char('Z')
    flakes = [Snowflake(app.width, app.height, ['*']) for _ in range(2)]
    output = []
    app.write_terminal = output.append

    with app.lock:
        app._draw_snow_locked([(flakes[0], 4, 3), (flakes[1], 4, 3)])
        app._erase_snow_locked()

    assert len(output) == 2
    assert output[1].count('\x1b[4;5H') == 1
    assert 'Z' in output[1]
    print("  PASS: overlapping snow restores its underlying cell once")

def test_ground_accumulates_bottom_then_top():
    """Test flakes pass through the first ground row and stack by column."""
    app = SnowyShell(chars=['*'])
    if not app.ground_rows:
        return
    flake = Snowflake(app.width, app.height, ['*'])
    x = 5

    position = app._settle_or_position(flake, x, app.shell_height)
    assert position == (flake, x, app.shell_height)
    assert app.ground == {}

    assert app._settle_or_position(flake, x, app.height - 1) is None
    assert app.ground[(x, app.height - 1)] == '*'
    flake.char = '+'
    assert app._settle_or_position(flake, x, app.shell_height) is None
    assert app.ground[(x, app.shell_height)] == '+'
    assert app._settle_or_position(flake, x, app.shell_height) is None
    assert len(app.ground) == 2
    print("  PASS: ground accumulates bottom then top")

def test_ground_clears_without_reclaiming_rows():
    """Test clearing accumulation leaves Unix shell geometry reserved."""
    app = SnowyShell(chars=['*'])
    if not app.ground_rows:
        return
    app.ground[(1, app.height - 1)] = '*'
    output = []
    app.write_terminal = output.append
    shell_height = app.shell_height

    with app.lock:
        app._clear_ground_locked()

    assert app.ground == {}
    assert app.shell_height == shell_height
    assert f'\x1b[{app.height};1H\x1b[2K' in output[0]
    print("  PASS: clearing ground preserves reserved shell geometry")

def test_ground_resize_resets_geometry():
    """Test resize clears accumulation and recomputes shell height."""
    app = SnowyShell(chars=['*'])
    app.ground[(1, app.height - 1)] = '*'
    app.write_terminal = lambda data: None

    with app.lock:
        app._resize_locked(100, 30)
    assert app.ground == {}
    assert (app.width, app.height, app.shell_height) == (100, 30, 28)
    assert app.screen_buffer.height == 28

    with app.lock:
        app._resize_locked(80, 6)
    assert app.ground_rows == 0
    assert app.shell_height == 6
    assert app.screen_buffer.height == 6
    print("  PASS: resize resets ground and handles short terminals")

def test_pty_output_filter_protects_ground_rows():
    """Test child margin resets cannot reclaim protected ground rows."""
    output_filter = PtyOutputFilter(22)
    assert output_filter.feed('\x1b[') == ''
    assert output_filter.feed('r') == '\x1b[1;22r'
    assert output_filter.feed('\x1b[2;99r') == '\x1b[2;22r'
    filtered = output_filter.feed('\x1b[?1049h')
    assert filtered.startswith('\x1b[?1049h')
    assert '\x1b[1;22r' in filtered
    filtered = output_filter.feed('\x1bc')
    assert filtered.startswith('\x1bc')
    assert '\x1b[1;22r' in filtered
    assert output_filter.feed('\x1bPpayload\x1b[') == ''
    assert output_filter.feed('r\x1b\\') == '\x1bPpayload\x1b[r\x1b\\'
    print("  PASS: PTY output filter protects ground rows")

def test_screen_buffer_respects_scrolling_region():
    """Test shell scrolling leaves physical ground rows untouched."""
    buf = ScreenBuffer(8, 5)
    parser = AnsiParser(buf)
    parser.feed('\x1b[1;3r')
    parser.feed('one\r\ntwo\r\nthree\r\nfour')
    assert ''.join(c.char for c in buf.cells[0]).startswith('two')
    assert ''.join(c.char for c in buf.cells[1]).startswith('three')
    assert ''.join(c.char for c in buf.cells[2]).startswith('four')
    assert all(c.char == ' ' for row in buf.cells[3:] for c in row)
    print("  PASS: screen buffer preserves rows below scrolling region")

def test_unix_overlay_uses_dec_cursor_save():
    """Test Unix overlay writes preserve cursor state with DEC sequences."""
    app = SnowyShell(density=1.0)
    output = []
    app.write_terminal = output.append

    app._write_overlay('\x1b[2;3H*')

    assert output == [DEC_SAVE_CURSOR + '\x1b[2;3H*' + DEC_RESTORE_CURSOR]
    print("  PASS: Unix overlay uses DEC cursor save and restore")

def test_snow_skips_last_column_on_unix():
    """Test snow cannot leave the terminal in an autowrap-pending state."""
    app = SnowyShell(density=1.0)
    flake = Snowflake(app.width, app.height, ['*'])
    output = []
    app.write_terminal = output.append

    with app.lock:
        app._draw_snow_locked([(flake, app.width - 1, app.height - 1)])

    assert output == []
    assert app.drawn_snow == {}
    print("  PASS: Unix snow skips the terminal's final column")

def test_ansi_parser_dec_cursor_save_restore():
    """Test screen tracking understands DEC overlay cursor preservation."""
    buf = ScreenBuffer(10, 5)
    parser = AnsiParser(buf)
    parser.feed('abc\x1b7\x1b[5;10H*\x1b8X')
    assert buf.get_cell(3, 0).char == 'X'
    print("  PASS: ANSI parser handles DEC cursor save and restore")

def test_sgr_dict_to_ansi():
    """Test SGR dict to ANSI conversion."""
    # Default (empty) SGR
    assert sgr_dict_to_ansi(None) == '', "None SGR should return empty"
    assert sgr_dict_to_ansi({}) == '', "Empty SGR should return empty"
    # Bold
    assert sgr_dict_to_ansi({'bold': True, 'italic': False, 'underline': False, 'fg': '', 'bg': ''}) == '\x1b[1m'
    # Foreground color
    assert sgr_dict_to_ansi({'bold': False, 'italic': False, 'underline': False, 'fg': '31', 'bg': ''}) == '\x1b[31m'
    # Bold + fg
    assert sgr_dict_to_ansi({'bold': True, 'italic': False, 'underline': False, 'fg': '32', 'bg': ''}) == '\x1b[1;32m'
    print("  PASS: sgr_dict_to_ansi works")

def test_terminal_cell():
    """Test TerminalCell."""
    cell = TerminalCell()
    assert cell.char == ' ', "Default char should be space"
    assert cell.sgr is None, "Default sgr should be None"
    cell.set('X', {'bold': True, 'fg': '31', 'bg': '', 'italic': False, 'underline': False})
    assert cell.char == 'X', "Char should be X"
    sgr_ansi = cell.get_sgr_ansi()
    assert '\x1b[1;31m' in sgr_ansi, f"SGR ANSI should include bold+red, got {sgr_ansi!r}"
    print("  PASS: terminal cell works")

def test_screen_buffer():
    """Test ScreenBuffer."""
    buf = ScreenBuffer(10, 5)
    # Write a character
    buf.set_cursor(0, 0)
    buf.process_sgr([1, 31])  # bold + red
    buf.write_char('A')
    cell = buf.get_cell(0, 0)
    assert cell is not None, "Cell should exist"
    assert cell.char == 'A', "Cell char should be A"
    assert cell.sgr is not None, "Cell sgr should be set"
    assert cell.sgr.get('bold') == True, "Cell should be bold"
    assert cell.sgr.get('fg') == '31', "Cell fg should be 31"
    print("  PASS: screen buffer works")

def test_ansi_parser_simple():
    """Test AnsiParser with simple text."""
    buf = ScreenBuffer(80, 24)
    parser = AnsiParser(buf)
    parser.feed("Hello")
    assert buf.get_cell(0, 0).char == 'H'
    assert buf.get_cell(4, 0).char == 'o'
    print("  PASS: ansi parser handles simple text")

def test_ansi_parser_split_sequences():
    """Test ANSI sequences split across PTY reads remain resumable."""
    buf = ScreenBuffer(80, 24)
    parser = AnsiParser(buf)
    parser.feed('\x1b[')
    assert parser.buffer == '\x1b['
    parser.feed('31mred\x1b]0;title')
    assert buf.get_cell(0, 0).char == 'r'
    assert buf.get_cell(0, 0).sgr.get('fg') == '31'
    assert parser.buffer == '\x1b]0;title'
    parser.feed('\x07X')
    assert buf.get_cell(3, 0).char == 'X'
    print("  PASS: ANSI parser resumes split CSI and OSC sequences")

def test_screen_buffer_scrolls():
    """Test that parsed output tracks terminal scrolling."""
    buf = ScreenBuffer(10, 2)
    parser = AnsiParser(buf)
    parser.feed("first\r\nsecond\r\nthird")
    assert ''.join(cell.char for cell in buf.cells[0]).startswith('second')
    assert ''.join(cell.char for cell in buf.cells[1]).startswith('third')
    print("  PASS: screen buffer handles terminal scrolling")

def test_ansi_parser_delays_autowrap():
    """Test a full-width line only wraps on the next printable character."""
    buf = ScreenBuffer(5, 2)
    parser = AnsiParser(buf)
    parser.feed('12345\rX')
    assert ''.join(cell.char for cell in buf.cells[0]) == 'X2345'
    assert ''.join(cell.char for cell in buf.cells[1]) == '     '
    assert (buf.cursor_x, buf.cursor_y) == (1, 0)
    print("  PASS: ANSI parser delays autowrap until printable input")

def test_ansi_parser_zsh_prompt_cleanup():
    """Test zsh's terminal-width prompt cleanup does not shift the screen."""
    buf = ScreenBuffer(10, 3)
    parser = AnsiParser(buf)
    parser.feed('%' + (' ' * 9) + '\r \r\rPROMPT> ')
    assert ''.join(cell.char for cell in buf.cells[0]).startswith('PROMPT> ')
    assert ''.join(cell.char for cell in buf.cells[1]) == ' ' * 10
    assert buf.cursor_y == 0
    print("  PASS: ANSI parser tracks zsh prompt cleanup")

if __name__ == '__main__':
    print("=" * 60)
    print("Testing snowy_shell.py")
    print("=" * 60)
    
    tests = [
        test_terminal_size,
        test_ansi_enable,
        test_snowflake,
        test_snowflake_drift,
        test_shell_detection,
        test_unix_shell_args,
        test_snowflake_init,
        test_write_terminal,
        test_batch_update,
        test_config_options,
        test_alt_screen,
        test_control_files,
        test_signal_handlers,
        test_snowflake_update_with_bounds,
        test_no_snow_option,
        test_unix_reserves_two_ground_rows,
        test_pid_tracking,
        test_read_char_at,
        test_snowflake_saved_char,
        test_snow_thread_saves_chars,
        test_pty_output_erases_snow_before_forwarding,
        test_overlapping_snow_restores_cell_once,
        test_ground_accumulates_bottom_then_top,
        test_ground_clears_without_reclaiming_rows,
        test_ground_resize_resets_geometry,
        test_pty_output_filter_protects_ground_rows,
        test_screen_buffer_respects_scrolling_region,
        test_unix_overlay_uses_dec_cursor_save,
        test_snow_skips_last_column_on_unix,
        test_ansi_parser_dec_cursor_save_restore,
        test_sgr_dict_to_ansi,
        test_terminal_cell,
        test_screen_buffer,
        test_ansi_parser_simple,
        test_ansi_parser_split_sequences,
        test_screen_buffer_scrolls,
        test_ansi_parser_delays_autowrap,
        test_ansi_parser_zsh_prompt_cleanup,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        print(f"\nRunning {test.__name__}...")
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    
    sys.exit(0 if failed == 0 else 1)
