"""Verifier for implement-anagram. Run with cwd = the task workspace.

Exit 0 == solved, non-zero == not solved.
"""
import sys

sys.path.insert(0, ".")

try:
    from anagram import are_anagrams
except Exception as e:
    print("import failed:", e)
    sys.exit(1)

CASES = [
    ("listen", "silent", True),
    ("Dormitory", "Dirty Room", True),
    ("hello", "world", False),
    ("", "", True),
    ("a", "a ", True),
    ("Conversation", "Voices rant on", True),
    ("abc", "abcd", False),
]

for a, b, expected in CASES:
    try:
        got = are_anagrams(a, b)
    except NotImplementedError:
        print("are_anagrams still raises NotImplementedError")
        sys.exit(1)
    except Exception as e:
        print(f"call raised on ({a!r}, {b!r}): {e}")
        sys.exit(1)
    if bool(got) != expected:
        print(f"FAIL ({a!r}, {b!r}): got {got!r}, expected {expected}")
        sys.exit(1)

print("all cases passed")
sys.exit(0)
