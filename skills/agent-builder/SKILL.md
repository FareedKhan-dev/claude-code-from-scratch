---
name: agent-builder
description: Use when building a new agent harness, designing tool systems, or structuring multi-agent workflows. Provides patterns, templates, and decision trees for harness engineering.
---

# Agent Builder Skill

## When to use this skill
Load this skill when the user wants to:
- Build a new agent from scratch
- Design a tool for an agent
- Structure a multi-agent system
- Debug an agent loop that isn't working
- Choose between agent architectures

## Core principle

The agent is always the model. Your job is the harness.

```
Harness = Tools + Knowledge + Observation + Action + Permissions
```

Never try to encode intelligence in your harness code. Give the model
clean tools, clear context, and get out of the way.

## The minimal agent (always start here)

```python
from anthropic import Anthropic
client = Anthropic()

def agent_loop(messages, tools, dispatch, system):
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            system=system, messages=messages,
            tools=tools, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = dispatch[block.name](block.input)
                results.append({"type": "tool_result",
                                 "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
```

Do not add anything until you need it. Every mechanism should earn its place.

## Tool design checklist

Before writing a tool, ask:
- [ ] Is the name a verb? (bash, read, write — not "file_manager")
- [ ] Does the description say WHEN to use it, not just what it does?
- [ ] Is the input schema minimal? No optional fields unless truly needed.
- [ ] Does it return plain text the model can reason about?
- [ ] Does it have a hard timeout?
- [ ] Is output truncated to a safe length (≤50k chars)?

## Tool description formula

```
"[Action verb] [what it does]. Use when [specific situation].
[What it returns]. [Any important limits]."
```

Example:
```
"Read a file and return numbered lines. Use when you need to inspect
file content or reference specific line numbers. Returns up to 50,000
characters. Use start_line/end_line for large files."
```

## Architecture decision tree

```
One task, one user, no persistence needed?
    → s01: minimal loop + bash

Need file read/write/search?
    → s02: extended tool dispatch

Need the agent to plan before acting?
    → s03: add todo_write tool

Task too big for one context window?
    → s04: subagent isolation

Need domain-specific knowledge?
    → s05: skill loading

Long-running session, context will overflow?
    → s06: compression + memory file

Complex multi-step project spanning sessions?
    → s07: task graph with dependencies

Slow operations (builds, tests)?
    → s08: background tasks

Work that parallelises across specialties?
    → s09+: agent teams with mailboxes

Need isolation between parallel tasks?
    → s12/s23: git worktrees
```

## Common mistakes

**Putting logic in the harness instead of trusting the model**
Bad:  `if "error" in output: retry_with_different_approach()`
Good: return the error to the model and let it decide

**Giant system prompts**
Bad:  5,000-word system prompt covering every scenario
Good: load domain knowledge on-demand via skills (s05)

**Blocking the loop on slow operations**
Bad:  `output = subprocess.run("npm test", timeout=300)`
Good: run in background thread, notify when done (s08)

**Shared mutable state between subagents**
Bad:  subagents writing to the same dict/file without locks
Good: each subagent has its own isolated context (s04, s12)

## Subagent pattern template

```python
def spawn_subagent(prompt: str, tools=EXTENDED_TOOLS, dispatch=EXTENDED_DISPATCH) -> str:
    messages = [{"role": "user", "content": prompt}]
    while True:
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM,
            messages=messages, tools=tools, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = dispatch_tools(response.content, dispatch)
        messages.append({"role": "user", "content": results})
    return "".join(b.text for b in messages[-1]["content"] if hasattr(b, "text"))
```
