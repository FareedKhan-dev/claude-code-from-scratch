#!/usr/bin/env python3
"""
s13_streaming.py: Implementation of Real-Time Token Streaming for AI Agents.

Motto: "Tokens print as they arrive — the terminal feels alive"

This module demonstrates the transition from synchronous, blocking API calls 
to a streaming architecture. In traditional requests (s01-s12), the user 
must wait for the entire completion to be generated before seeing any text. 
With streaming, partial text chunks are processed and displayed as soon as 
the model emits them.

Key Architectural Concepts:
    1. Event-Driven UI: Utilizing the `text_stream` iterator to provide 
       immediate visual feedback in the terminal.
    2. Context Managers: Using the `with` statement to ensure the streaming 
       connection is properly established and closed.
    3. State Finalization: Using `stream.get_final_message()` to retrieve 
       the complete Message object (including metadata and tool calls) after 
        the stream concludes.
    4. Perceived Latency: Reducing the "Time To First Token" (TTFT) to 
       milliseconds, making the agent feel more responsive and interactive.

Technological Note:
    While `core.stream_loop()` handles this under the hood for most sessions, 
    this script makes the implementation explicit for educational purposes.
"""

# === Standard Library Imports ===
import sys  # System-specific parameters and functions (for exit/flush)
from typing import List, Dict, Any, Optional  # For robust type hinting

# === Local Module Imports ===
# We pull standard configuration and toolsets from the centralized core module.
from core import (
    client,            # Pre-configured Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    DEFAULT_SYSTEM,    # Persona instructions for the agent
    EXTENDED_TOOLS,    # The full suite of file/shell tool schemas
    EXTENDED_DISPATCH, # Mapping for tool implementations
    dispatch_tools     # Logic to execute requested tools
)


# === Explicit Streaming Logic ===

def agent_loop_streaming(messages: List[Dict[str, Any]]) -> None:
    """
    Executes a Thinking-Acting cycle using real-time token streaming.

    This function manually manages the stream events, printing text chunks 
    to the terminal immediately as they are received from the API.

    Args:
        messages (List[Dict[str, Any]]): The shared conversation history. 
                                          Modified in-place.
    """
    # Continue the loop as long as the model wants to use tools (autonomous turns)
    while True:
        # Visual indicator that the model is processing input
        print("\n\033[36m> Thinking...\033[0m")
        
        # Initialize a streaming message request
        # The 'with' block ensures the HTTP connection is managed safely
        with client.messages.stream(
            model=MODEL,               # The LLM engine
            system=DEFAULT_SYSTEM,     # Persona and behavioral constraints
            messages=messages,         # History for context
            tools=EXTENDED_TOOLS,      # Available capabilities
            max_tokens=8000,           # Response limit
        ) as stream:
            # Iterate over the text fragments as they arrive from the Anthropic API
            for text in stream.text_stream:
                # Print the partial text without a newline
                # flush=True ensures the token appears immediately in the terminal
                print(text, end="", flush=True)
            
            # After the stream finishes, retrieve the fully assembled Message object
            # This object contains the full 'text' and any 'tool_use' blocks
            response = stream.get_final_message()
            
        # Add a newline after the full assistant response is printed
        print()
        
        # Update the conversation history with the assistant's response
        # Required for the model to know what it previously said/did
        messages.append({"role": "assistant", "content": response.content})
        
        # Evaluate if the model is finished or if it requested tools
        if response.stop_reason != "tool_use":
            # Exit the loop if there are no more actions to take
            return
            
        # Execute the tool calls requested by the model
        # dispatch_tools returns a list of tool_result blocks
        results: List[Dict[str, Any]] = dispatch_tools(response.content, EXTENDED_DISPATCH)
        
        # Feed the results back into the conversation as a new 'user' turn
        messages.append({"role": "user", "content": results})


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal-based REPL for the s13 streaming session.
    """
    # UI Header in Gray
    print("\033[90ms13: streaming | tokens print as they arrive\033[0m\n")
    
    # Initialize session history
    history: List[Dict[str, Any]] = []

    # Main Command Loop
    while True:
        try:
            # User Prompt in Cyan
            query: str = input("\033[36ms13 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Handle Ctrl+C/D gracefully
            print("\nExiting session.")
            sys.exit(0)

        # Basic exit handling
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query
        history.append({"role": "user", "content": query})
        
        # Start the streaming agent loop
        agent_loop_streaming(history)
        
        # Visual spacing for the next turn
        print()


if __name__ == "__main__":
    # Standard entry point protection
    main()