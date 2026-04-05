#!/usr/bin/env python3
"""
s07_task_system.py: Implementation of a Persistent, Dependency-Aware Task Graph.

Motto: "Break big goals into small tasks, order them, persist to disk"

This module evolves the planning capability of the agent by moving from a 
simple list (s03) to a robust, file-based task management system. It allows 
the agent to handle complex projects by defining tasks that depend on the 
completion of others.

Key Architectural Concepts:
    1. Directed Acyclic Graph (DAG): Tasks can depend on one or more previous 
       tasks, creating a logical order of operations.
    2. Unique Identification: Every task is assigned a short, unique UUID 
       to prevent ambiguity during updates.
    3. State Persistence: The entire graph is serialized to `.agent_tasks.json`, 
       enabling the agent to resume work across restarts or hand work 
       off to other agents (s09+).
    4. Dependency Resolution: The `task_next` tool provides automated 
       logic to identify the next actionable item that isn't blocked.

Task Schema:
    - ID: 8-character unique hex string.
    - Status: [pending, in_progress, done, failed].
    - Dependencies: List of IDs that must be 'done' before this task starts.
"""

# === Standard Library Imports ===
import os      # Operating system interfaces
import json    # JSON serialization for task persistence
import uuid    # Unique identifier generation
import sys     # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Union, Optional, Set  # For strict type hinting

# === Local Module Imports ===
from core import (
    EXTENDED_TOOLS,      # Base tools (bash, read, write, etc.)
    EXTENDED_DISPATCH,   # Mapping for base tools
    stream_loop          # The core autonomous loop logic
)

# === Configuration and Constants ===

# File where the task graph is persisted.
TASKS_FILE: Path = Path(".agent_tasks.json")

# Specialized System Prompt: Instructs the agent on task management protocol.
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use the task system to manage complex work: create tasks, respect dependencies, "
    "and mark progress. Always call task_list or task_next before starting work "
    "to ensure you are working on the correct unblocked priority."
)

# === Task Graph I/O Helpers ===

def _load_tasks() -> List[Dict[str, Any]]:
    """
    Reads the task graph from the local JSON file.

    Returns:
        List[Dict[str, Any]]: The list of task objects. Returns empty list if 
                              file is missing or corrupt.
    """
    if not TASKS_FILE.exists():
        return []
    try:
        # Load and parse the JSON task list
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        # Fallback for corrupted files
        return []


def _save_tasks(tasks: List[Dict[str, Any]]) -> None:
    """
    Serializes the current task graph to the local JSON file.

    Args:
        tasks (List[Dict[str, Any]]): The list of tasks to persist.
    """
    try:
        # Write with indentation for human-readability (debugging)
        TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    except IOError as e:
        print(f"\033[31m[error] Failed to save tasks: {e}\033[0m")


# === Tool Implementations ===

def run_task_create(description: str, depends_on: Optional[List[str]] = None, priority: str = "medium") -> str:
    """
    Creates a new task and adds it to the persistent graph.

    Args:
        description (str): Text describing the work to be done.
        depends_on (List[str], optional): List of IDs this task depends on.
        priority (str): Level of importance [high, medium, low].

    Returns:
        str: Success message including the generated task ID.
    """
    tasks = _load_tasks()
    
    # Generate a unique 8-character ID for the task
    task_id = uuid.uuid4().hex[:8]
    
    new_task = {
        "id":          task_id,
        "description": description,
        "status":      "pending",
        "priority":    priority,
        "depends_on":  depends_on or [],
        "result":      "", # To be filled upon completion
    }
    
    tasks.append(new_task)
    _save_tasks(tasks)
    
    return f"Created task {task_id}: {description}"


def run_task_list() -> str:
    """
    Generates a formatted summary of all tasks in the system.

    Returns:
        str: A table-like string showing status, priority, deps, and description.
    """
    tasks = _load_tasks()
    if not tasks:
        return "(no tasks currently in the system)"
    
    lines = []
    for t in tasks:
        # Format dependencies for display
        deps_str = f" [needs: {','.join(t['depends_on'])}]" if t.get("depends_on") else ""
        # Create a structured line for terminal output
        line = f"[{t['id']}] [{t['status']:12s}] [{t['priority']:6s}]{deps_str} {t['description']}"
        lines.append(line)
        
    return "\n".join(lines)


def run_task_update(task_id: str, status: str, result: str = "") -> str:
    """
    Updates the status or recorded result of an existing task.

    Args:
        task_id (str): The unique ID (or prefix) of the task to update.
        status (str): The new status [pending, in_progress, done, failed].
        result (str, optional): A summary of the work performed.

    Returns:
        str: Success or error message.
    """
    tasks = _load_tasks()
    found = False
    
    for t in tasks:
        # Support updating by full ID or a unique prefix
        if t["id"].startswith(task_id):
            t["status"] = status
            if result:
                t["result"] = result
            found = True
            actual_id = t["id"]
            break
            
    if found:
        _save_tasks(tasks)
        return f"Task {actual_id} successfully updated to '{status}'"
    
    return f"Error: Task with ID '{task_id}' not found."


def run_task_next() -> str:
    """
    Algorithm to find the next actionable task based on dependencies.

    This identifies 'pending' tasks where all prerequisite tasks are 'done'.

    Returns:
        str: The description of the next task or a status message.
    """
    tasks = _load_tasks()
    
    # Create a set of IDs for tasks that are fully completed
    done_ids: Set[str] = {t["id"] for t in tasks if t["status"] == "done"}
    
    for t in tasks:
        # We only care about tasks that haven't started yet
        if t["status"] != "pending":
            continue
            
        # Check if every dependency for this task is in the 'done_ids' set
        dependencies = t.get("depends_on", [])
        if all(dep in done_ids for dep in dependencies):
            return f"Suggested Next Task: [{t['id']}] (Priority: {t['priority']}) - {t['description']}"
            
    return "No unblocked tasks available. Either all tasks are done or there is a dependency circularity."


# === Tool Schema and Dispatch Extensions ===

# Define the task-related tools for the Anthropic API
TASK_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "task_create",
        "description": "Create a new task in the persistent dependency graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What needs to be done."},
                "depends_on":  {
                    "type": "array", 
                    "items": {"type": "string"},
                    "description": "List of task IDs this task depends on."
                },
                "priority":    {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["description"],
        },
    },
    {
        "name": "task_list",
        "description": "Show all tasks, their IDs, status, and dependency requirements.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "task_update",
        "description": "Change the status of a task or record its final result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "8-char ID of the task."},
                "status":  {"type": "string", "enum": ["pending", "in_progress", "done", "failed"]},
                "result":  {"type": "string", "description": "Brief summary of work done."},
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "task_next",
        "description": "Consult the graph logic to find the next task that is not blocked by dependencies.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# Map the task tools to their Python implementations
TASK_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Inherit base tools
    "task_create": lambda inp: run_task_create(
        inp["description"], 
        inp.get("depends_on"), 
        inp.get("priority", "medium")
    ),
    "task_list":   lambda inp: run_task_list(),
    "task_update": lambda inp: run_task_update(
        inp["task_id"], 
        inp["status"], 
        inp.get("result", "")
    ),
    "task_next":   lambda inp: run_task_next(),
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s07 'Task System' agent.
    """
    # UI Header in Gray
    print(f"\033[90ms07: file-based task graph | tasks → {TASKS_FILE}\033[0m\n")
    
    # Interaction history for the current session
    history: List[Dict[str, Any]] = []

    # Main Command Loop (REPL)
    while True:
        try:
            # User Prompt in Cyan
            query: str = input("\033[36ms07 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit handlers
            print("\nExiting session.")
            sys.exit(0)

        # Standard exit check
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record query
        history.append({"role": "user", "content": query})
        
        # Start the autonomous loop with the new task tools and system prompt
        stream_loop(
            messages=history,
            tools=TASK_TOOLS,
            dispatch=TASK_DISPATCH,
            system=SYSTEM
        )
        
        # Visual spacer
        print()


if __name__ == "__main__":
    # Script entry point
    main()