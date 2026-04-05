#!/usr/bin/env python3
"""
s04_subagent.py: Implementation of Recursive Agent Delegation and Isolation.

Motto: "Break big tasks down; each subtask gets a clean context"

This module introduces the concept of 'Subagents'. When a task is too complex, 
messy, or exploratory, the Lead Agent can delegate it to a Subagent. 

Key Architectural Concepts:
    1. Context Isolation: The Subagent starts with a brand new, empty 
       conversation history. It does not see the parent's previous messages.
    2. Context Protection: This prevents the 'Lead' agent's context window from 
       being filled with debugging logs, trial-and-error attempts, or 
       irrelevant file reads.
    3. Delegation: The model acts as a project manager, using the 
       `spawn_subagent` tool to offload specific technical hurdles.

State Mapping:
    - Parent messages[]: [User Request, Asst: "I will spawn a subagent", 
                          User: (Subagent Result), Asst: "Done"]
    - Subagent messages[]: [Subtask prompt, Tool calls..., Final Answer]
"""

# === Standard Library Imports ===
import os      # Operating system interfaces (for pathing and environment)
import sys     # System-specific parameters (for exiting)
from typing import List, Dict, Any, Union, Optional  # Type hinting for robust code

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # Model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    dispatch_tools,    # Logic to execute tool calls
    stream_loop        # The main autonomous loop logic
)

# === Configuration and Constants ===

# System prompt for the 'Lead' agent (The Manager)
SYSTEM: str = (
    f"You are a lead coding agent at {os.getcwd()}. "
    "For complex or isolated subtasks, use spawn_subagent to delegate. "
    "Subagents run in a fresh context — perfect for exploration or risky operations."
)

# System prompt for the 'Subagent' (The Specialist)
SUBAGENT_SYSTEM: str = (
    f"You are a subagent working on a specific subtask at {os.getcwd()}. "
    "Complete your task thoroughly. Summarize your result clearly at the end."
)


# === Subagent Logic ===

def run_subagent(prompt: str) -> str:
    """
    Spawns an isolated agent loop to handle a specific subtask.

    This function creates a secondary conversation history and runs its own 
    autonomous 'Thinking-Acting' cycle until completion. It returns only 
    the final textual conclusion to the parent.

    Args:
        prompt (str): The specific instruction/task for the subagent.

    Returns:
        str: The final text result produced by the subagent.
    """
    # UI Notification: Print in Magenta (\033[35m) to distinguish from the Lead Agent
    print(f"\033[35m  [subagent] spawned for: {prompt[:60]}...\033[0m")
    
    # Initialize a FRESH, isolated message history for the subagent
    sub_messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]

    # The Subagent Loop (Synchronous execution within the tool call)
    while True:
        # Step 1: Call the LLM with the specialist's system prompt and fresh history
        response = client.messages.create(
            model=MODEL,
            system=SUBAGENT_SYSTEM,
            messages=sub_messages,
            tools=EXTENDED_TOOLS, # Subagents have full access to standard tools
            max_tokens=8000,
        )
        
        # Step 2: Record the subagent's response to its internal history
        sub_messages.append({"role": "assistant", "content": response.content})
        
        # Step 3: Check if the subagent is finished (stop_reason isn't tool_use)
        if response.stop_reason != "tool_use":
            break
        
        # Step 4: If the subagent wants to use tools, execute them using the standard dispatch
        results: List[Dict[str, Any]] = dispatch_tools(response.content, EXTENDED_DISPATCH)
        
        # Step 5: Append results back to the subagent's private history
        sub_messages.append({"role": "user", "content": results})

    # Final Step: Extract the text components from the subagent's last message
    # We ignore tool_use blocks here to provide a clean summary to the parent
    final_result: str = "".join(
        block.text for block in sub_messages[-1]["content"]
        if hasattr(block, "text")
    )
    
    # UI Notification: Log completion in Magenta
    print(f"\033[35m  [subagent] done: {final_result[:100]}...\033[0m")
    
    return final_result


# === Tool Schema and Dispatch Extensions ===

# Define the tools available to the 'Lead' Agent, including subagent spawning.
SUBAGENT_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "spawn_subagent",
        "description": (
            "Spawn a fresh subagent to handle a subtask in an isolated context. "
            "Use for exploration, risky operations, or tasks that shouldn't pollute "
            "the main conversation history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string", 
                    "description": "Detailed instructions for the subagent."
                }
            },
            "required": ["prompt"],
        },
    },
]

# Map the 'spawn_subagent' tool name to the 'run_subagent' Python function.
SUBAGENT_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Include bash, read, write, etc.
    "spawn_subagent": lambda inp: run_subagent(inp["prompt"]),
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s04 'Delegation' agent.
    """
    # Visual header in Gray (\033[90m)
    print("\033[90ms04: subagent isolation | spawn_subagent → fresh context\033[0m\n")
    
    # Initialize the main (parent) conversation history
    history: List[Dict[str, Any]] = []

    # Main Command Loop (REPL)
    while True:
        try:
            # Prompt user in Cyan
            query: str = input("\033[36ms04 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Handle exit signals
            print("\nExiting session.")
            sys.exit(0)

        # Check for exit command
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query in main history
        history.append({"role": "user", "content": query})
        
        # Start the Lead Agent's autonomous loop
        # Note: We use the SUBAGENT_TOOLS and SUBAGENT_DISPATCH sets here.
        stream_loop(
            messages=history,
            tools=SUBAGENT_TOOLS,
            dispatch=SUBAGENT_DISPATCH,
            system=SYSTEM
        )
        
        # Visual spacing
        print()


if __name__ == "__main__":
    # Standard Python entry point
    main()