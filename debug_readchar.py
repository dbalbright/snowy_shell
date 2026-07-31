import os
import sys
import ctypes

def read_char_at(x, y):
    """Read the character at terminal position (x, y)."""
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

            class COORD(ctypes.Structure):
                _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

            char = ctypes.create_unicode_buffer(1)
            length = ctypes.c_ulong(0)
            coord = COORD(X=x, Y=y)
            result = kernel32.ReadConsoleOutputCharacterW(
                handle, char, 1, coord, ctypes.byref(length))
            print(f"  read_char_at({x}, {y}): result={result}, length={length.value}, char={char.value!r}", file=sys.stderr)
            return char.value if length.value > 0 else ' '
        except Exception as e:
            print(f"  read_char_at({x}, {y}): exception={e}", file=sys.stderr)
            pass
    return ' '

# Write some text to the terminal
sys.stdout.write("Hello World!\n")
sys.stdout.flush()

# Read characters at specific positions
print("Reading characters:", file=sys.stderr)
for i in range(12):
    c = read_char_at(i, 0)
    print(f"  Position ({i}, 0): {c!r}", file=sys.stderr)
