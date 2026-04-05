#!/usr/bin/env python3
"""
s02_tool_use.py: Implementation of the Scalable Tool Dispatch Map Pattern.

Motto: "Adding a tool means adding one handler"

This script demonstrates the second evolution of the AI agent architecture. 
Unlike s01, which had a hardcoded tool loop, s02 utilizes a 'Dispatch Map' 
strategy. This decouples the agent's decision-making process from the 
execution of specific tasks.

Key Architectural Concepts:
    1. Separation of Concerns: The agent loop (stream_loop) handles the 
       conversation flow, while the Dispatch Map (EXTENDED_DISPATCH) handles 
       the execution of specific capabilities.
    2. Scalability: To add a new capability (e.g., database access, web search), 
       a developer only needs to update the tool schema and the dispatch map. 
       The core logic remains untouched.
    3. Abstraction: By using the `stream_loop` from the `core` module, we 
       centralize the complexity of streaming API responses and recursive 
       tool calls.
"""

# === Standard Library Imports ===
import sys  # System-specific parameters and functions
from typing import List, Dict, Any  # Support for complex type annotations

# === Local Module Imports ===
# We import the extended toolset and the centralized streaming loop logic.
from core import (
    EXTENDED_TOOLS,      # List of JSON schemas for: bash, read, write, grep, glob, revert
    EXTENDED_DISPATCH,   # Dictionary mapping tool names to their Python implementations
    stream_loop          # The abstracted Thinking-Acting loop with streaming support
)

# === Main Execution Block ===

def main() -> None:
    """
    Runs the main interactive Command Line Interface (CLI) for the s02 agent.

    This function initializes the session state (history) and enters a 
    Read-Eval-Print Loop (REPL). It captures user input and hands over 
    control to the `stream_loop` for autonomous tool execution.
    """
    # Print a professional header to indicate the active session and available tools.
    # \033[90m is a terminal escape code for Gray text.
    print("\033[90ms02: dispatch map | tools: bash, read, write, grep, glob, revert\033[0m\n")

    # Initialize the conversation history as a list of dictionaries.
    # This list acts as the agent's "short-term memory" for the current session.
    history: List[Dict[str, Any]] = []

    # Enter the main interactive loop to process consecutive user queries.
    while True:
        try:
            # Display a cyan-colored prompt (\033[36m) to the user and capture input.
            query: str = input("\033[36ms02 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Gracefully handle exit signals:
            # EOFError: User pressed Ctrl+D
            # KeyboardInterrupt: User pressed Ctrl+C
            print("\nExiting agent session.")
            sys.exit(0)

        # Check if the user wants to terminate the session or sent an empty string.
        if not query or query.lower() in ("q", "exit", "quit"):
            print("Session terminated.")
            break

        # Record the user's query into the history list.
        # This is the 'User Role' entry that starts the next model turn.
        history.append({"role": "user", "content": query})

        # --- The Core Logic of Session 02 ---
        # Instead of manually checking tool_use (as in s01), we call `stream_loop`.
        # This function will:
        #   1. Call the Anthropic API with the current history.
        #   2. Stream the text output to the console in real-time.
        #   3. If the model calls a tool, look up the function in `EXTENDED_DISPATCH`.
        #   4. Execute the tool, append results, and repeat until the task is done.
        stream_loop(
            messages=history,            # Pass the mutated state
            tools=EXTENDED_TOOLS,        # Pass the full suite of file/shell tools
            dispatch=EXTENDED_DISPATCH   # Provide the routing map for those tools
        )
        
        # Print a newline to separate conversation turns for better terminal readability.
        print()


if __name__ == "__main__":
    # Standard Python entry point protection.
    # This ensures main() is only invoked if the file is run as a script, 
    # allowing the logic to be imported into other sessions without execution.
    main()