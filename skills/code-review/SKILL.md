---
name: code-review
description: Use when asked to review code, audit a file for bugs, check code quality, or suggest improvements. Provides a structured review process and issue categorisation.
---

# Code Review Skill

## When to use this skill
Load when the user asks you to:
- Review a file or function for bugs
- Check code quality or style
- Suggest improvements or refactors
- Audit security or error handling
- Compare two implementations

## Review process

Always follow this order. Do not skip steps.

### Step 1 — Read before commenting
Use the `read` tool to read the full file first.
Use `grep` to find related code (callers, tests, imports).
Never comment on code you haven't fully read.

### Step 2 — Understand intent
Ask: what is this code trying to do?
Read any docstrings, comments, and function names.
If the intent is unclear, note it — don't assume.

### Step 3 — Categorise issues

Use these categories consistently:

| Category | When to use |
|----------|-------------|
| BUG      | Code that will produce wrong results or crash |
| SECURITY | Input not validated, secrets exposed, injection risks |
| PERF     | Unnecessary work, wrong data structure, O(n²) that could be O(n) |
| STYLE    | Inconsistent naming, long functions, missing docstrings |
| SUGGEST  | Optional improvements — not required to fix |

### Step 4 — Write findings

Format each finding:

```
[CATEGORY] file.py:line_number
  Issue: one sentence describing the problem
  Why:   why this matters
  Fix:   concrete suggestion or corrected code snippet
```

### Step 5 — Summary

End with:
- Total issues found per category
- The most critical issue (if any BUG or SECURITY)
- Whether the code is safe to deploy as-is

## What good code review looks like

- Specific: cite file + line number, not "somewhere in the code"
- Actionable: every issue has a suggested fix
- Proportionate: distinguish blocking bugs from style nits
- Respectful: review the code, not the author

## Common bugs to look for in Python

```python
# Mutable default argument (very common)
def append(item, lst=[]):   # BUG: lst shared across all calls
    lst.append(item)

# Exception swallowed silently
try:
    do_something()
except Exception:            # BUG: hides errors, use `except Exception as e: log(e)`
    pass

# Off-by-one in slices
items[1:len(items)]          # STYLE: prefer items[1:]

# Late binding closure
fns = [lambda: i for i in range(5)]   # BUG: all return 4
fns = [lambda i=i: i for i in range(5)]  # Fix

# Forgetting to close resources
f = open("file.txt")        # BUG: use `with open("file.txt") as f:`
```

## Common bugs to look for in agents

- Tool outputs not checked before use (model assumes success)
- No timeout on subprocess calls (agent hangs forever)
- Output not truncated (enormous tool results fill context)
- Missing tool error handling (exception crashes the loop)
- No dangerous command filter in bash tool

## Security checklist

- [ ] No secrets, tokens, or keys in code or comments
- [ ] Shell commands not built from user input (injection risk)
- [ ] File paths validated (no `../../../etc/passwd` traversal)
- [ ] External input not eval'd or exec'd
- [ ] Dependencies pinned to versions in requirements.txt
