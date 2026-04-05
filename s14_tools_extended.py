#!/usr/bin/env python3
"""
s14_tools_extended.py: Implementation of a Safety-First Extended Tool Arsenal.

Motto: "More hands, but every touch is reversible"

This module demonstrates the practical application of the 'Extended Toolset' 
defined in core.py. While previous sessions relied heavily on raw 'bash' 
commands, s14 promotes specialized tools (read, write, grep, glob) that 
provide structured outputs and automatic state recovery.

Key Architectural Concepts:
    1. Automated Snapshots: Every call to 'write' automatically backs up the 
       original file content to an in-memory store before overwriting.
    2. Reversibility: The 'revert' tool allows the agent to undo its last 
       change if a code edit introduces bugs or fails a test.
    3. Structured Output: The 'read' tool provides 1-indexed line numbers, 
       making it easier for the LLM to identify specific locations for edits 
       compared to raw 'cat' output.
    4. Efficiency: 'grep' and 'glob' provide faster, more reliable file 
       discovery than piping multiple shell commands together.

Why prefer these over Bash?
    Raw bash commands like `sed` or `echo > file` are destructive and 
    opaque to the harness. Specialized tools provide the harness with 
    hooks for logging, security filtering, and state management.
"""

# === Standard Library Imports ===
import os      # Operating system interfaces (for environment and pathing)
import sys     # System-specific parameters and functions (for exit/handling)
from typing import List, Dict, Any, Optional  # For strict type hinting

# === Local Module Imports ===
# We import the full arsenal of tools and the core streaming logic.
from core import (
    EXTENDED_TOOLS,      # JSON schemas for: read, write, grep, glob, revert, bash
    EXTENDED_DISPATCH,   # Dictionary mapping tool names to Python handlers
    stream_loop          # The abstracted Thinking-Acting loop with streaming
)

# === Configuration and Constants ===

# Specialized System Prompt: Instructs the agent on the "Safety-First" protocol.
# It encourages the use of managed tools over raw, un-monitored shell commands.
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}. "
    "You have access to a suite of specialized file tools. "
    "PREFER using 'read', 'write', 'grep', and 'glob' over raw bash commands "
    "for file operations. These tools provide better formatting and "
    "automatic snapshots. If you make a mistake or break the code, "
    "use the 'revert' tool to restore the previous state immediately."
)

# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s14 'Extended Tools' agent.
    
    This function manages the Read-Eval-Print Loop (REPL), capturing user 
    queries and delegating the autonomous execution to the stream_loop.
    """
    # UI Header: Extract names of active tools for display in Gray (\033[90m)
    active_tool_names: List[str] = [tool["name"] for tool in EXTENDED_TOOLS]
    print(f"\033[90ms14: extended tools | {', '.join(active_tool_names)} | snapshots active\033[0m\n")
    
    # Initialize the conversation history list
    # This maintains the context of the current session
    history: List[Dict[str, Any]] = []

    # Main Command Loop
    while True:
        try:
            # Display a cyan-colored prompt (\033[36m) for the user
            query: str = input("\033[36ms14 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Gracefully handle terminal exit signals (Ctrl+C, Ctrl+D)
            print("\nExiting session.")
            sys.exit(0)

        # Check for exit commands or empty input
        if not query or query.lower() in ("q", "exit", "quit"):
            print("Goodbye.")
            break

        # Append the user's text query to the session history
        history.append({"role": "user", "content": query})
        
        # Trigger the agentic loop
        # stream_loop will:
        #   1. Prompt the model with the history and tool schemas.
        #   2. Stream text responses back to the user.
        #   3. Execute tools (with auto-snapshots for 'write') as requested.
        #   4. Maintain the conversation until the model reaches a final answer.
        stream_loop(
            messages=history,
            tools=EXTENDED_TOOLS,
            dispatch=EXTENDED_DISPATCH,
            system=SYSTEM
        )
        
        # Print a newline for visual separation between conversation turns
        print()


if __name__ == "__main__":
    # Standard Python entry point protection
    # Ensures the REPL starts only when the file is run directly
    main()