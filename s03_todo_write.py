#!/usr/bin/env python3
"""
s03_todo_write.py: Implementation of Agentic Planning and State Tracking.

Motto: "An agent without a plan drifts"

This module introduces a task management system that the AI agent must use to 
organize complex, multi-step operations. By persisting a plan to a JSON file, 
the agent creates a "Source of Truth" for its progress, which significantly 
reduces hallucinations and logic errors in long-running tasks.

New Capabilities:
    1. todo_write: Serializes a list of task descriptions to a JSON file.
    2. todo_read: Deserializes and displays the current state of the plan.
    3. todo_update: Modifies the status of specific tasks (pending -> done).

The core of this session is the 'System Prompt' which mandates the use of 
these tools, effectively creating a "Think-Plan-Act" pipeline.
"""

# === Standard Library Imports ===
import os      # Operating system interfaces (for pathing and environment)
import json    # JSON encoding and decoding for the todo file
import sys     # System-specific parameters (for exiting)
from typing import List, Dict, Any, Union, Optional  # Type hinting for robust code

# === Local Module Imports ===
from core import (
    EXTENDED_TOOLS,      # Base tools (bash, read, write, etc.)
    EXTENDED_DISPATCH,   # Mapping for base tools
    stream_loop          # The core autonomous loop logic
)

# === Configuration and Constants ===

# Path to the persistent storage for the agent's plan.
# Using a hidden file avoids cluttering the user's workspace.
TODO_FILE: str = ".agent_todo.json"

# Specialized System Prompt: This is the "Policy" the agent follows.
# It explicitly instructs the model to create a plan before acting.
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}. "
    "Before working on any multi-step task, ALWAYS call todo_write first "
    "to write your plan. Then execute each step and call todo_update after each one. "
    "This ensures you stay on track and don't skip steps."
)

# === Todo Tool Implementations ===

def run_todo_write(tasks: List[str]) -> str:
    """
    Initializes a new task plan and saves it to the persistent JSON store.

    This function wipes any existing plan and starts fresh with the 
    provided list of task descriptions.

    Args:
        tasks (List[str]): A list of strings describing each step of the plan.

    Returns:
        str: A human-readable confirmation of the written plan.
    """
    # Transform raw strings into a list of structured dictionaries with metadata
    data = [
        {"id": i, "task": t, "status": "pending"} 
        for i, t in enumerate(tasks)
    ]
    
    # Context manager ensures the file is closed properly after writing
    with open(TODO_FILE, "w", encoding="utf-8") as f:
        # Write the JSON data with indentation for human readability if opened manually
        json.dump(data, f, indent=2)
    
    # Construct a formatted preview of the plan for the agent's context
    lines = "\n".join(f"  [{i}] {t}" for i, t in enumerate(tasks))
    return f"Plan written ({len(tasks)} tasks):\n{lines}"


def run_todo_read() -> str:
    """
    Reads and returns the current state of the task plan.

    Used by the agent to remind itself of the next step or overall progress.

    Returns:
        str: A formatted string representing the todo list or a 'not found' message.
    """
    try:
        # Open and load the existing JSON plan
        with open(TODO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Format each task with its ID and status (padded to 12 chars for alignment)
        return "\n".join(
            f"[{t['id']}] [{t['status']:12s}] {t['task']}" for t in data
        )
    except FileNotFoundError:
        # Graceful handling if the agent tries to read before writing
        return "(no todo list found - please use todo_write first)"
    except Exception as e:
        # General error fallback
        return f"Error reading todo list: {e}"


def run_todo_update(index: int, status: str) -> str:
    """
    Updates the completion status of a specific task within the plan.

    Args:
        index (int): The numeric ID (0-based) of the task to modify.
        status (str): The new status string (e.g., 'in_progress', 'done').

    Returns:
        str: A confirmation message or an error description.
    """
    try:
        # Read the current state to perform an in-memory update
        with open(TODO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Verify the index exists in the list to prevent out-of-bounds errors
        if 0 <= index < len(data):
            # Update the status value for the specific dictionary entry
            data[index]["status"] = status
            
            # Persist the modified list back to disk
            with open(TODO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            return f"Updated task {index} status to: {status}"
        
        # Error if the provided index is invalid
        return f"Error: Task index {index} is out of range."
        
    except FileNotFoundError:
        return "Error: No todo list found to update."
    except Exception as e:
        return f"Error during todo_update: {e}"


# === Tool Schema and Dispatch Extensions ===

# We define the JSON schemas for the new tools.
# These schemas tell the LLM exactly what arguments to provide.
TODO_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "todo_write",
        "description": "Write a multi-step todo plan before starting a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array", 
                    "items": {"type": "string"},
                    "description": "A list of sequential steps to complete the goal."
                }
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "todo_read",
        "description": "Read the current todo list to check progress.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "todo_update",
        "description": "Update the status of a specific task in the plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index":  {
                    "type": "integer", 
                    "description": "The 0-based index of the task."
                },
                "status": {
                    "type": "string", 
                    "enum": ["pending", "in_progress", "done"],
                    "description": "The new status of the task."
                },
            },
            "required": ["index", "status"],
        },
    },
]

# We expand the dispatch map by merging the existing map with our new handlers.
# This keeps the agent's core capabilities available while adding planning.
TODO_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Unpack existing tools (bash, read, write, etc.)
    "todo_write":  lambda inp: run_todo_write(inp["tasks"]),
    "todo_read":   lambda inp: run_todo_read(),
    "todo_update": lambda inp: run_todo_update(inp["index"], inp["status"]),
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s03 'Planning' agent.
    """
    # UI Header
    print("\033[90ms03: plan before execute | todo_write + todo_update\033[0m\n")
    
    # Local session history
    history: List[Dict[str, Any]] = []

    # Main REPL
    while True:
        try:
            # User Prompt in Cyan
            query: str = input("\033[36ms03 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)

        # Basic exit conditions
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Capture user intent
        history.append({"role": "user", "content": query})
        
        # Execute the agent loop. 
        # Crucially, we pass the custom SYSTEM prompt here to enforce planning behavior.
        stream_loop(
            messages=history,
            tools=TODO_TOOLS,
            dispatch=TODO_DISPATCH,
            system=SYSTEM
        )
        
        # Formatting spacer
        print()


if __name__ == "__main__":
    # Standard entry point execution
    main()