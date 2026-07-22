"""Verifier for fix-palindrome. Run with cwd = the task workspace.

Exit 0 == solved, non-zero == not solved. Kept out of the workspace so the
agent never sees the test cases it is graded on.
"""
import sys

sys.path.insert(0, ".")

try:
    from palindrome import is_palindrome
except Exception as e:  # import/syntax error == not solved
    print("import failed:", e)
    sys.exit(1)

CASES = [
    ("A man, a plan, a canal: Panama", True),
    ("racecar", True),
    ("hello", False),
    ("", True),
    ("No 'x' in Nixon", True),
    ("abca", False),
    ("Was it a car or a cat I saw?", True),
]

for text, expected in CASES:
    try:
        got = is_palindrome(text)
    except Exception as e:
        print(f"call raised on {text!r}: {e}")
        sys.exit(1)
    if bool(got) != expected:
        print(f"FAIL {text!r}: got {got!r}, expected {expected}")
        sys.exit(1)

print("all cases passed")
sys.exit(0)
