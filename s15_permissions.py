#!/usr/bin/env python3
"""
s15_permissions.py: Implementation of Rule-Based Permission Governance.

Motto: "Trust is earned; every action is judged before it runs"

This module introduces a "Guarded Dispatch" pattern. In previous sessions, 
tool calls were executed immediately. s15 wraps every tool execution with 
a security check that evaluates the command against a YAML-based policy 
configuration (`config/permissions.yaml`).

Governance Tiers (evaluated in order):
    1. always_deny: Immediate block. The agent is informed of the policy violation.
    2. always_allow: Silent pass. Typically used for read-only or safe commands.
    3. ask_user: Interactive confirmation. The terminal pauses and prompts the 
       human operator for a (y/N) decision.

Architectural Concept:
    By wrapping the dispatch map with a `_guarded` function, we separate the 
    logic of "what the tool does" from "who is allowed to do it," allowing 
    for centralized security audits without modifying tool code.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import sys          # System-specific parameters and functions
from typing import List, Dict, Any, Callable, Tuple, Optional  # For robust type hinting

# === Local Module Imports ===
# We import both the tool definitions and the permission logic from core.
from core import (
    EXTENDED_TOOLS,      # JSON schemas for the agent tools
    stream_loop,         # The core autonomous loop logic
    run_bash,            # Tool implementation: shell commands
    run_read,            # Tool implementation: file reading
    run_write,           # Tool implementation: file writing
    run_grep,            # Tool implementation: regex searching
    run_glob,            # Tool implementation: file pattern matching
    run_revert,          # Tool implementation: state restoration
    load_rules,          # Logic to parse config/permissions.yaml
    check_permission     # Logic to evaluate a command against rules
)

# === Configuration and Initialization ===

# Load the permission rules from the external YAML file at script startup.
# This ensures that policies are consistent across the session.
RULES: Dict[str, Any] = load_rules()

# Define the agent's persona, acknowledging the security constraints.
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}. "
    "Security protocols are active. Some of your commands may require "
    "explicit user approval via a [PERMISSION] prompt. If a command is denied, "
    "seek an alternative way to accomplish the task within the permitted bounds."
)

# === Permission Wrapper Logic ===

def _guarded(tool_name: str, handler_fn: Callable[[Dict[str, Any]], str], inp: Dict[str, Any]) -> str:
    """
    Wraps a tool execution with a security permission check.

    This acts as a middleware between the LLM's request and the actual 
    Python execution of the tool.

    Args:
        tool_name (str): The name of the tool (e.g., 'bash').
        handler_fn (Callable): The actual Python function to execute if permitted.
        inp (Dict[str, Any]): The dictionary of arguments provided by the LLM.

    Returns:
        str: The output of the tool if allowed, or a 'Blocked' message if denied.
    """
    # Identify the primary 'target' of the command for rule matching.
    # We take the first value in the input dictionary (usually 'command' or 'path').
    # If no input exists, we fallback to checking the tool_name itself.
    check_str: str = str(list(inp.values())[0]) if inp else tool_name
    
    # Call core.check_permission to evaluate the action against the loaded YAML rules.
    # The function handles console output for DENIED or ASK_USER states.
    is_allowed, reason = check_permission(tool_name, check_str, RULES)
    
    if not is_allowed:
        # If the check returns False, we short-circuit and never call handler_fn.
        # This prevents the potentially dangerous operation from ever starting.
        return f"Blocked by Permission Policy: {check_str[:80]} (Reason: {reason})"
    
    # If allowed, proceed to execute the actual tool logic.
    return handler_fn(inp)


# === Guarded Dispatch Map ===

# We rebuild the dispatch map, wrapping every single entry with the _guarded function.
# Each lambda here acts as a closure, preserving the specific tool implementation.
PERM_DISPATCH: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "bash":   lambda inp: _guarded(
        "bash",   lambda i: run_bash(i["command"]), inp
    ),
    "read":   lambda inp: _guarded(
        "read",   lambda i: run_read(i.get("path"), i.get("start_line"), i.get("end_line")), inp
    ),
    "write":  lambda inp: _guarded(
        "write",  lambda i: run_write(i.get("path"), i.get("content")), inp
    ),
    "grep":   lambda inp: _guarded(
        "grep",   lambda i: run_grep(i.get("pattern"), i.get("path", "."), i.get("recursive", True)), inp
    ),
    "glob":   lambda inp: _guarded(
        "glob",   lambda i: run_glob(i.get("pattern")), inp
    ),
    "revert": lambda inp: _guarded(
        "revert", lambda i: run_revert(i.get("path")), inp
    ),
}

# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s15 'Permissioned' agent.
    
    This function manages the Read-Eval-Print Loop (REPL), capturing user 
    queries and delegating the autonomous execution to the stream_loop 
    using the security-hardened PERM_DISPATCH.
    """
    # UI Header: Indicate where the rules are being sourced from.
    print("\033[90ms15: permission governance | rules from config/permissions.yaml\033[0m\n")
    
    # Initialize session history
    history: List[Dict[str, Any]] = []

    # Main Command-Line Loop
    while True:
        try:
            # User Prompt in Cyan (\033[36m)
            query: str = input("\033[36ms15 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Gracefully handle terminal exit signals (Ctrl+C, Ctrl+D)
            print("\nExiting session.")
            sys.exit(0)

        # Basic session exit handlers
        if not query or query.lower() in ("q", "exit", "quit"):
            print("Session ended.")
            break

        # Record the user's query in the interaction history
        history.append({"role": "user", "content": query})
        
        # Execute the agentic loop.
        # Note: We pass PERM_DISPATCH instead of the raw EXTENDED_DISPATCH.
        # This ensures every model turn is subjected to the permission guard.
        stream_loop(
            messages=history,
            tools=EXTENDED_TOOLS,
            dispatch=PERM_DISPATCH,
            system=SYSTEM
        )
        
        # Visual spacing between conversation turns
        print()


if __name__ == "__main__":
    # Standard entry point protection
    # Ensures the REPL starts only when the script is executed directly.
    main()