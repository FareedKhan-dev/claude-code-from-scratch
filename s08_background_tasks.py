#!/usr/bin/env python3
"""
s08_background_tasks.py: Implementation of Asynchronous Shell Command Execution.

Motto: "Run slow operations in the background; the agent keeps thinking"

This module introduces a non-blocking execution model for the agent. In previous 
sessions, calling a tool like 'bash' would block the agent until the command 
finished. For operations that take minutes (e.g., full test suites, large 
installations), this is inefficient.

Key Architectural Concepts:
    1. Multi-threading: Background tasks are spawned in 'daemon' threads, 
       ensuring they don't block the main application flow.
    2. Thread-Safe Communication: A `queue.Queue` is used to safely pass 
       completion notifications from background threads back to the main thread.
    3. Event Injection: Between interaction turns, the system checks the 
       notification queue and "injects" completed results into the agent's 
       conversation history as fresh user input.
    4. Non-Blocking Feedback: The tool returns immediately with a confirmation, 
       allowing the agent to proceed with other sub-tasks.

State Mapping:
    - Main Loop: Handles user interaction and tool calls.
    - Worker Threads: Execute long-running shell commands.
    - Notification Queue: Bridges the gap between the two.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import threading   # Support for concurrent thread execution
import queue       # Thread-safe FIFO queue for notifications
import subprocess  # Subprocess management for shell commands
import sys         # System-specific parameters and functions
from typing import List, Dict, Any, Union, Optional  # For strict type hinting

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # Model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    dispatch_tools,    # Logic to execute tool calls
    stream_loop        # The core autonomous loop logic
)

# === Configuration and Global State ===

# A thread-safe queue to store messages from background tasks
# Key: string (the formatted completion message)
_NOTIFY_QUEUE: queue.Queue = queue.Queue()

# Specialized System Prompt: Instructs the agent on when to use backgrounding
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash_background for slow operations like tests, builds, or long scripts. "
    "This tool returns immediately. The result of the command will be provided "
    "to you automatically via a notification in a later turn. "
    "While waiting, you should continue working on other available tasks."
)

# === Background Task Logic ===

def run_bash_background(command: str, label: Optional[str] = None) -> str:
    """
    Executes a shell command in a background daemon thread.

    This function triggers the command and returns immediately with a 
    tracking label, preventing the agent from stalling.

    Args:
        command (str): The shell command to execute.
        label (str, optional): A short name to identify this task in logs.

    Returns:
        str: A confirmation message for the agent's context.
    """
    # Default label to a snippet of the command if none provided
    task_label = label or command[:40]

    def _worker_logic():
        """Internal function intended to run inside a separate thread."""
        print(f"\033[90m  [bg] started: {task_label}\033[0m")
        try:
            # Execute the command synchronously within this background thread
            # We set a 300s timeout to prevent zombie processes
            result = subprocess.run(
                command, 
                shell=True, 
                capture_output=True,
                text=True, 
                timeout=300, 
                cwd=os.getcwd()
            )
            # Capture and truncate output to avoid context window flooding (2k chars)
            output = (result.stdout + result.stderr).strip()[:2000] or "(no output)"
            status = "completed"
        except subprocess.TimeoutExpired:
            output = "Error: Process exceeded 300s timeout limit."
            status = "timed out"
        except Exception as e:
            output = f"Error: Unexpected failure during execution: {e}"
            status = "failed"

        # Format the notification for the agent and put it in the thread-safe queue
        notification = f"[Background task '{task_label}' {status}]\n{output}"
        _NOTIFY_QUEUE.put(notification)

    # Initialize the thread. 'daemon=True' ensures the thread dies if the main app exits.
    worker_thread = threading.Thread(target=_worker_logic, daemon=True)
    worker_thread.start()
    
    return f"Background task started: '{task_label}'. You will be notified when it finishes."


def _drain_notifications() -> List[Dict[str, str]]:
    """
    Checks the notification queue and converts pending items into message blocks.

    Returns:
        List[Dict[str, str]]: A list of message objects formatted as 'user' role entries.
    """
    notifs = []
    # Exhaust the queue completely before returning
    while not _NOTIFY_QUEUE.empty():
        try:
            # Get notification without blocking
            msg = _NOTIFY_QUEUE.get_nowait()
            # Log to terminal for user visibility (Gray text)
            print(f"\033[90m  [bg] notification received: {msg[:80]}...\033[0m")
            # Wrap in a standard message format
            notifs.append({"role": "user", "content": msg})
        except queue.Empty:
            break
    return notifs


# === Tool Schema and Dispatch Extensions ===

# Define the tools available to the agent, including the backgrounding capability
BG_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "bash_background",
        "description": "Run a shell command in the background. Useful for long-running scripts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string", 
                    "description": "The command line string to execute."
                },
                "label": {
                    "type": "string", 
                    "description": "A short identifier for the notification."
                },
            },
            "required": ["command"],
        },
    },
]

# Map the background tool name to the execution logic
BG_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Include standard bash, read, etc.
    "bash_background": lambda inp: run_bash_background(
        inp["command"], 
        inp.get("label", "")
    ),
}

# === Enhanced Agent Loop ===

def agent_loop_with_bg(messages: List[Dict[str, Any]]) -> None:
    """
    A specialized agent turn that handles asynchronous notification injection.

    This function runs the standard loop, but immediately after the turn 
    finishes, it checks for completed background tasks. If found, it 
    automatically triggers another agent turn so the model can react.

    Args:
        messages (List[Dict[str, Any]]): The session's conversation history.
    """
    # 1. Execute the standard autonomous turn (Thinking -> Acting -> Thinking)
    stream_loop(messages, BG_TOOLS, BG_DISPATCH, system=SYSTEM)
    
    # 2. Check if any background tasks finished while the agent was working/waiting
    pending_notifications = _drain_notifications()
    
    # 3. If notifications exist, append them and force an immediate follow-up turn
    for notif in pending_notifications:
        messages.append(notif)
        print("\033[94m  [auto-turn] Processing background notification...\033[0m")
        # Recursively call the loop to process the new information
        stream_loop(messages, BG_TOOLS, BG_DISPATCH, system=SYSTEM)


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s08 'Backgrounding' agent.
    """
    # Visual UI Header
    print("\033[90ms08: background tasks | bash_background → notified on completion\033[0m\n")
    
    # Initialize the session's interaction history
    history: List[Dict[str, Any]] = []

    # Main Command Loop (REPL)
    while True:
        try:
            # User Input in Cyan
            query: str = input("\033[36ms08 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit handling
            print("\nExiting session.")
            sys.exit(0)

        # Standard exit handlers
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query
        history.append({"role": "user", "content": query})
        
        # Trigger the specialized background-aware loop
        agent_loop_with_bg(history)
        
        # Visual spacing
        print()


if __name__ == "__main__":
    # Script entry point
    main()