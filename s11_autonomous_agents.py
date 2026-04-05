#!/usr/bin/env python3
"""
s11_autonomous_agents.py: Implementation of Atomic Task Claiming and Self-Organization.

Motto: "Teammates scan the board and claim tasks themselves"

This module evolves the multi-agent architecture from "Delegation" (s09/s10) to 
"Autonomy." In this model, the Lead Agent does not manage individuals; it 
manages the Task Board. Background agents (Workers) poll the board, resolve 
dependencies, and claim tasks using thread-safe locking.

Key Architectural Concepts:
    1. Shared Task Board: A centralized JSON file acting as a "Blackboard" 
       architecture for agent coordination.
    2. Atomic Claiming: Uses `threading.Lock` to ensure that even with multiple 
       agents polling simultaneously, a task is never assigned to more than 
       one worker (preventing race conditions).
    3. Self-Organization: Agents identify "unblocked" tasks (where dependencies 
       are 'done') and move them from 'pending' to 'in_progress'.
    4. Resilience: If an agent fails during a task, the status is marked as 
       'failed', allowing for manual or automated retry logic.

Operational Flow:
    - Lead: Posts a high-level goal broken into task graph entries.
    - Workers: Continuously poll the board -> Claim task -> Execute -> Post Result.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import json        # JSON serialization for the task board
import threading   # Support for concurrent background agents
import time        # Polling delays and timing
import uuid        # Unique identifier generation for new tasks
import sys         # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Union, Optional  # For strict type hinting

# === Local Module Imports ===
# We import core execution logic to drive the autonomous worker turns.
from core import (
    client,            # The Anthropic API client
    MODEL,             # Model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    dispatch_tools,    # Logic to execute tool calls requested by workers
    stream_loop        # The core autonomous loop logic for the Lead
)

# === Configuration and Global State ===

# The shared persistent storage for the task graph (introduced in s07)
TASKS_FILE: Path = Path(".agent_tasks.json")

# Global lock to synchronize file access across multiple background threads
_TASKS_LOCK: threading.Lock = threading.Lock()

# === Task Board I/O (Thread-Safe) ===

def _load_tasks() -> List[Dict[str, Any]]:
    """
    Reads the current task list from the persistent JSON file.
    
    Returns:
        List[Dict[str, Any]]: The list of all tasks in the system.
    """
    if not TASKS_FILE.exists():
        return []
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return []


def _save_tasks(tasks: List[Dict[str, Any]]) -> None:
    """
    Serializes and saves the task list to the persistent JSON file.
    
    Args:
        tasks (List[Dict[str, Any]]): The updated list of tasks.
    """
    TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


# === Atomic State Transition Logic ===

def claim_next_task(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomically searches for and claims the next unblocked 'pending' task.

    A task is considered unblocked if all its IDs in 'depends_on' are marked 'done'.

    Args:
        agent_id (str): The unique name of the agent attempting the claim.

    Returns:
        Optional[Dict[str, Any]]: The claimed task object, or None if no tasks available.
    """
    # Enter critical section to prevent double-claiming by separate threads
    with _TASKS_LOCK:
        tasks = _load_tasks()
        
        # Identify all successfully completed task IDs
        done_ids = {t["id"] for t in tasks if t["status"] == "done"}
        
        for t in tasks:
            # We only care about tasks awaiting an agent
            if t["status"] != "pending":
                continue
                
            # Check if all dependencies are satisfied
            dependencies = t.get("depends_on", [])
            if all(dep in done_ids for dep in dependencies):
                # Transition state immediately to prevent other agents from seeing it as pending
                t["status"] = "in_progress"
                t["claimed_by"] = agent_id
                _save_tasks(tasks)
                return t
                
    return None


def complete_task(task_id: str, result: str) -> None:
    """
    Updates a task to 'done' status and records the final output.

    Args:
        task_id (str): The unique ID of the finished task.
        result (str): The textual summary of the completed work.
    """
    with _TASKS_LOCK:
        tasks = _load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = "done"
                t["result"] = result
                _save_tasks(tasks)
                break


def fail_task(task_id: str, error_message: str) -> None:
    """
    Updates a task to 'failed' status and records the error details.

    Args:
        task_id (str): The unique ID of the failed task.
        error_message (str): Information regarding why the task crashed.
    """
    with _TASKS_LOCK:
        tasks = _load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = "failed"
                t["error"] = error_message
                _save_tasks(tasks)
                break


# === Autonomous Worker Implementation ===

def run_autonomous_agent(agent_name: str, stop_event: threading.Event) -> None:
    """
    The main execution loop for an autonomous background agent.

    This function runs in its own thread, continuously looking for work 
    until the 'stop_event' is triggered.

    Args:
        agent_name (str): Unique name/ID for this worker instance.
        stop_event (threading.Event): Signal used to gracefully shut down the thread.
    """
    # Specialist system prompt for the worker
    system_instructions = (
        f"You are autonomous worker agent '{agent_name}' at {os.getcwd()}. "
        "Your goal is to process tasks claimed from the board thoroughly and correctly. "
        "Use your tools to achieve the task description provided."
    )
    
    print(f"\033[90m  [{agent_name}] online — polling for tasks...\033[0m")
    
    while not stop_event.is_set():
        # Attempt to claim a piece of work
        task = claim_next_task(agent_name)
        
        if not task:
            # No work available; back off for 1 second to save CPU/API calls
            stop_event.wait(timeout=1.0)
            continue

        # Log that the worker has started processing
        print(f"\033[35m  [{agent_name}] claimed: [{task['id']}] {task['description'][:60]}...\033[0m")
        
        # Initialize message history for the specific claimed task
        worker_messages: List[Dict[str, Any]] = [{"role": "user", "content": task["description"]}]
        
        try:
            # Autonomous "Think-Act" cycle for the claimed task
            while True:
                response = client.messages.create(
                    model=MODEL,
                    system=system_instructions,
                    messages=worker_messages,
                    tools=EXTENDED_TOOLS,
                    max_tokens=4000,
                )
                worker_messages.append({"role": "assistant", "content": response.content})
                
                # Check if the model has finished its task
                if response.stop_reason != "tool_use":
                    break
                
                # Execute worker-requested tools (bash, read, write, etc.)
                tool_results = dispatch_tools(response.content, EXTENDED_DISPATCH)
                worker_messages.append({"role": "user", "content": tool_results})
            
            # Extract final text and update the shared task board
            final_output = "".join(b.text for b in worker_messages[-1]["content"] if hasattr(b, "text"))
            complete_task(task["id"], final_output)
            print(f"\033[32m  [{agent_name}] completed: {task['id']}\033[0m")
            
        except Exception as e:
            # If the LLM loop crashes, mark the task as failed so it can be investigated
            fail_task(task["id"], str(e))
            print(f"\033[31m  [{agent_name}] failed: {task['id']} — {e}\033[0m")


# === Lead Agent Toolset (Management) ===

def _post_new_task(description: str, depends_on: Optional[List[str]] = None, priority: str = "medium") -> str:
    """
    Internal logic to add a new task to the shared board.
    """
    with _TASKS_LOCK:
        tasks = _load_tasks()
        task_id = uuid.uuid4().hex[:8]
        new_task = {
            "id":          task_id, 
            "description": description,
            "status":      "pending", 
            "priority":    priority,
            "depends_on":  depends_on or [], 
            "result":      ""
        }
        tasks.append(new_task)
        _save_tasks(tasks)
        
    return f"Task posted with ID {task_id}: {description}"


# Lead-specific tool definitions
LEAD_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "post_task", 
        "description": "Post a new task to the shared board for autonomous workers to claim.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "description": {"type": "string", "description": "What needs to be done."},
                "depends_on": {
                    "type": "array", 
                    "items": {"type": "string"},
                    "description": "List of task IDs this task depends on."
                },
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            }, 
            "required": ["description"]
        }
    },
    {
        "name": "task_status", 
        "description": "Show the current state of the entire task board.",
        "input_schema": {"type": "object", "properties": {}}
    },
]

# Map lead tools to implementations
LEAD_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH,
    "post_task": lambda inp: _post_new_task(
        inp["description"], 
        inp.get("depends_on"), 
        inp.get("priority", "medium")
    ),
    "task_status": lambda inp: "\n".join(
        f"[{t['id']}] [{t['status']:12}] {t['description']}" for t in _load_tasks()
    ) or "(the task board is currently empty)"
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction and manages the lifecycle of autonomous background agents.
    """
    # 1. Thread and Agent Configuration
    NUM_WORKERS: int = 2
    stop_signal = threading.Event()
    worker_threads: List[threading.Thread] = []

    # 2. Spawn Autonomous Background Agents
    for i in range(NUM_WORKERS):
        worker_id = f"agent-{i+1}"
        thread = threading.Thread(
            target=run_autonomous_agent,
            args=(worker_id, stop_signal),
            daemon=True # Threads will stop when the main process dies
        )
        thread.start()
        worker_threads.append(thread)

    # UI Header
    print(f"\033[90ms11: {NUM_WORKERS} autonomous agents scanning task board | post_task to add work\033[0m\n")
    
    # 3. Lead Agent REPL
    lead_system_prompt: str = (
        f"You are a lead orchestrator at {os.getcwd()}. "
        "Your role is to analyze user requests and use 'post_task' to break "
        "them into logical steps. Do NOT perform implementation tasks yourself; "
        "let your autonomous team handle the claiming and execution of work."
    )
    
    history: List[Dict[str, Any]] = []
    
    try:
        while True:
            try:
                # User Prompt in Cyan
                query: str = input("\033[36ms11 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nInitiating shutdown...")
                break

            # Handle session exit
            if not query or query.lower() in ("q", "exit", "quit"):
                break

            # Process Lead turn
            history.append({"role": "user", "content": query})
            stream_loop(
                messages=history,
                tools=LEAD_TOOLS,
                dispatch=LEAD_DISPATCH,
                system=lead_system_prompt
            )
            print()

    finally:
        # 4. Graceful Cleanup
        stop_signal.set()
        print("  Background workers signaled to stop. Cleaning up session.")


if __name__ == "__main__":
    # Script entry point
    main()