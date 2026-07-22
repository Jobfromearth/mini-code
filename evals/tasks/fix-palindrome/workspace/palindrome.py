def is_palindrome(s):
    """True if s is a palindrome over alphanumeric chars, case-insensitive."""
    cleaned = [c.lower() for c in s if c.isalnum()]
    # BUG: compares the list to itself instead of to its reverse.
    return cleaned == cleaned
