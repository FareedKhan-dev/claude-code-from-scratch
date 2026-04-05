#!/usr/bin/env python3
"""
s01_perception_action_loop.py: The Fundamental AI Agent Interaction Loop.

Motto: "One loop & bash is all you need"

This script serves as the entry point for the agent series, demonstrating the 
foundational "Thinking-Acting" cycle. It implements a synchronous interaction 
pattern where the LLM is prompted, evaluates if it needs to use a tool (specifically 
the 'bash' tool in this session), executes that tool, and continues until a 
final text response is produced.

The loop handles:
    1. Message history state management.
    2. API communication with Anthropic.
    3. Conditional logic based on 'stop_reason'.
    4. Synchronous tool dispatching.
"""

# === Standard Library Imports ===
import sys  # System-specific parameters and functions
from typing import List, Dict, Any, Union, Optional  # For robust type hinting

# === Local Module Imports ===
# We import core components defined in core.py to maintain DRY (Don't Repeat Yourself) principles.
from core import (
    client,           # Pre-configured Anthropic API client
    MODEL,            # The specific model ID (e.g., Claude 3.5 Sonnet)
    DEFAULT_SYSTEM,   # The system prompt defining the agent's persona
    BASIC_TOOLS,      # JSON schema for the 'bash' tool
    BASIC_DISPATCH,   # Dictionary mapping "bash" -> run_bash function
    dispatch_tools    # Logic to parse tool_use blocks and execute handlers
)


# === Core Agent Logic ===

def agent_loop(messages: List[Dict[str, Any]], dispatch: Dict[str, Any]) -> None:
    """
    Executes the autonomous interaction cycle between the User and the LLM.

    This function implements a 'While-True' loop that persists as long as the 
    Model indicates it needs to perform actions (tool_use). It manages the 
    transition of state by appending assistant responses and tool results 
    to the conversation history.

    Args:
        messages (List[Dict[str, Any]]): The shared conversation history. 
            This list is modified in-place.
        dispatch (Dict[str, Any]): A mapping of tool names to callable 
            Python functions that handle the model's requests.

    Returns:
        None: The function modifies the input 'messages' list directly.
    """
    # Enter the infinite loop of the agent's "thought" process
    while True:
        # Visual feedback for the user to indicate the LLM is processing
        print("\n\033[36m> Thinking...\033[0m") 
        
        # Request a completion from the Anthropic API
        # We pass the history, the system prompt, and the tool definitions
        response = client.messages.create(
            model=MODEL,             # Specify the AI model to use
            system=DEFAULT_SYSTEM,   # Provide high-level instructions
            messages=messages,       # Pass the full chat history for context
            tools=BASIC_TOOLS,       # Inform the model about available capabilities
            max_tokens=8000,         # Set a high limit for long-running tasks
        )

        # Record the model's full response (text and tool_use blocks) into history
        # The API requires this to maintain context for subsequent turns
        messages.append({"role": "assistant", "content": response.content})

        # Evaluate the 'stop_reason' provided by the API
        # 'tool_use' means the model wants to execute a function before continuing
        if response.stop_reason != "tool_use":
            # If the reason is 'end_turn' or 'max_tokens', we exit the loop
            break

        # If we reached this point, the model has requested one or more tool calls.
        # dispatch_tools iterates over response.content, finds 'tool_use', and runs it.
        results: List[Dict[str, Any]] = dispatch_tools(response.content, dispatch)
        
        # Append the results of the tool execution back into the history.
        # This is sent as a 'user' role but contains 'tool_result' blocks.
        messages.append({"role": "user", "content": results})


# === User Interface & Main Execution ===

def main() -> None:
    """
    Initializes the terminal-based REPL (Read-Eval-Print Loop) for the agent.

    This function handles user input, maintains the session history, and 
    triggers the agent_loop for every user query. It provides basic error 
    handling for keyboard interrupts and exits.
    """
    # Header display for the console
    print("\033[90ms01: one loop + bash = an agent\033[0m\n")
    
    # Initialize an empty list to store the conversation transcript
    history: List[Dict[str, Any]] = []

    # Persistent loop to keep the interactive session alive
    while True:
        try:
            # Display a cyan-colored prompt to the user
            query: str = input("\033[36ms01 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Gracefully handle Ctrl+D or Ctrl+C to exit the program
            print("\nExiting session.")
            sys.exit(0)

        # Check for exit commands or empty input
        if not query or query.lower() in ("q", "exit", "quit"):
            print("Goodbye.")
            break

        # Append the user's raw text input to the session history
        history.append({"role": "user", "content": query})

        # Trigger the agentic loop which may involve multiple model/tool turns
        agent_loop(history, BASIC_DISPATCH)

        # Post-loop: Extract and display the final answer to the user
        # The last item in history is the assistant's final response
        last_message: Dict[str, Any] = history[-1]
        
        print("\n\033[32mFinal Answer:\033[0m")
        
        # Iterate through response content blocks to find and print only text
        # (Filtering out any tool_use metadata for a clean UI)
        for block in last_message.get("content", []):
            # Ensure the block is a text-type block as defined by Anthropic API
            if hasattr(block, 'type') and block.type == "text":
                print(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                print(block.get("text"))
        
        # Visual spacing for the next prompt
        print()


if __name__ == "__main__":
    # Standard Python entry point protection
    # Ensures main() only runs if the script is executed directly
    main()