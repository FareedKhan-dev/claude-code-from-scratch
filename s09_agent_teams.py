#!/usr/bin/env python3
"""
s09_agent_teams.py: Implementation of a Persistent Multi-Agent Team Framework.

Motto: "When the task is too big for one, delegate to teammates"

This module introduces a "Lead-specialist" architecture. Unlike s04 subagents, 
which were ephemeral and spawned on-demand, these teammates are persistent 
background threads with specific roles. They communicate using "Mailboxes" 
implemented as JSONL files.

Key Architectural Concepts:
    1. Role Specialization: Agents are defined with specific personas 
       (e.g., Explorer, Writer) to improve accuracy in specific domains.
    2. Asynchronous Mailboxes: Communication is decoupled using the 
       filesystem. A sender writes a JSONL entry to a recipient's inbox; 
       the recipient polls, processes, and writes a reply back.
    3. Concurrency: Teammates run in dedicated background threads, allowing 
       the Lead agent to delegate tasks without losing its own state.
    4. Synthesis: The Lead agent acts as an orchestrator, receiving raw 
       data from teammates and synthesizing it into a final answer for the user.

Teaching Note:
    This version uses JSONL for simplicity and visibility. In production 
    environments (see s22), this is typically replaced with a robust 
    Message Broker like Redis or RabbitMQ.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import json        # JSON serialization for message passing
import threading   # Support for concurrent agent execution
import time        # Time utilities for polling delays
import sys         # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
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

# Directory where agent communication files (mailboxes) are stored
MAILBOX_DIR: Path = Path(".mailboxes")
MAILBOX_DIR.mkdir(exist_ok=True) # Ensure directory exists at startup

# Definitions of available teammates and their specialized system prompts
TEAMMATES: Dict[str, str] = {
    "explorer": (
        f"You are an explorer agent specializing in code comprehension at {os.getcwd()}. "
        "Your goal is to find relevant files, explain logic, and map dependencies. "
        "Use bash, read, glob, and grep to gather intelligence."
    ),
    "writer": (
        f"You are a writer agent specializing in file creation and editing at {os.getcwd()}. "
        "Your goal is to implement features, fix bugs, and document code. "
        "Use write, read, and bash to modify the environment."
    ),
}

# The Lead Agent's persona and instructions
SYSTEM: str = (
    f"You are a lead coding agent at {os.getcwd()}. "
    f"You have a team of specialists: {', '.join(TEAMMATES.keys())}. "
    "For complex tasks, delegate sub-problems to the appropriate specialist. "
    "Once they reply, synthesize their findings into a cohesive final response."
)

# === Messaging Layer (Mailbox System) ===

def _get_mailbox_path(agent_name: str) -> Path:
    """Helper to resolve the mailbox file path for a specific agent."""
    return MAILBOX_DIR / f"{agent_name}.jsonl"


def _send_message(to_agent: str, from_agent: str, body: str) -> None:
    """
    Appends a structured message to the recipient's JSONL mailbox.

    Args:
        to_agent (str): Recipient agent name.
        from_agent (str): Sender agent name.
        body (str): The task description or result data.
    """
    message_data = {
        "from": from_agent,
        "body": body,
        "timestamp": time.time()
    }
    # Open in append mode ('a') to handle multiple incoming messages
    with open(_get_mailbox_path(to_agent), "a", encoding="utf-8") as f:
        f.write(json.dumps(message_data) + "\n")


def _receive_messages(agent_name: str) -> List[Dict[str, str]]:
    """
    Reads all messages from an agent's mailbox and clears it.

    Args:
        agent_name (str): The name of the agent checking their inbox.

    Returns:
        List[Dict[str, str]]: A list of message dictionaries.
    """
    path = _get_mailbox_path(agent_name)
    if not path.exists():
        return []
    
    try:
        # Read all lines, parse JSON, and filter out empty lines
        lines = path.read_text(encoding="utf-8").splitlines()
        messages = [json.loads(line) for line in lines if line.strip()]
        # Clear the mailbox after reading (Atomic 'pop all' simulation)
        path.write_text("", encoding="utf-8")
        return messages
    except (json.JSONDecodeError, IOError) as e:
        print(f"\033[31m  [error] Mailbox read failed for {agent_name}: {e}\033[0m")
        return []


# === Teammate Agent Thread Logic ===

def _run_teammate_loop(name: str, specialist_prompt: str, stop_event: threading.Event) -> None:
    """
    Persistent background loop for teammate agents.

    This function polls the mailbox for tasks, executes an autonomous 
    agent turn for each task, and sends the result back to the sender.

    Args:
        name (str): The name of the specialist agent.
        specialist_prompt (str): The system prompt defining their role.
        stop_event (threading.Event): Signal to terminate the thread.
    """
    print(f"\033[90m  [{name}] thread initialized and ready\033[0m")
    
    while not stop_event.is_set():
        # Check for incoming messages
        incoming = _receive_messages(name)
        
        for msg in incoming:
            sender = msg["from"]
            task_body = msg["body"]
            
            print(f"\033[35m  [{name}] processing task from {sender}: {task_body[:60]}...\033[0m")
            
            # Create a fresh context for this specific delegated task
            sub_history: List[Dict[str, Any]] = [{"role": "user", "content": task_body}]
            
            # Autonomous turn-taking for the specialist
            while True:
                response = client.messages.create(
                    model=MODEL,
                    system=specialist_prompt,
                    messages=sub_history,
                    tools=EXTENDED_TOOLS, # Specialists have standard file tools
                    max_tokens=4000,
                )
                sub_history.append({"role": "assistant", "content": response.content})
                
                # Exit turn if the model is done (no more tool calls)
                if response.stop_reason != "tool_use":
                    break
                
                # Execute tool calls requested by the specialist
                results = dispatch_tools(response.content, EXTENDED_DISPATCH)
                sub_history.append({"role": "user", "content": results})
            
            # Extract the final textual conclusion
            final_text = "".join(
                block.text for block in sub_history[-1]["content"] 
                if hasattr(block, "text")
            )
            
            # Send the result back to the sender's mailbox
            _send_message(to_agent=sender, from_agent=name, body=final_text)
            print(f"\033[35m  [{name}] result sent back to {sender}\033[0m")
        
        # Avoid high-CPU spin-locking with a short sleep
        stop_event.wait(timeout=0.5)


# === Tool Implementations for Lead Agent ===

def run_send_to_teammate(name: str, message: str) -> str:
    """
    Lead Agent tool to delegate work to a specialist.

    This function sends the message and then polls the Lead's mailbox 
    until a reply is received or a timeout occurs.

    Args:
        name (str): Name of the teammate to contact.
        message (str): Instructions for the teammate.

    Returns:
        str: The teammate's response or an error message.
    """
    if name not in TEAMMATES:
        return f"Error: '{name}' is not a recognized teammate."
    
    # Send the task to the teammate
    _send_message(to_agent=name, from_agent="lead", body=message)
    
    # Synchronous Polling (The Lead blocks until the teammate finishes)
    # In a production system, this could be made async.
    print(f"\033[90m  [lead] waiting for {name} to reply...\033[0m")
    for attempt in range(60): # 60-second total timeout
        time.sleep(1)
        replies = _receive_messages("lead")
        if replies:
            # Aggregate all messages received (usually just one)
            return "\n\n".join(f"Response from {r['from']}:\n{r['body']}" for r in replies)
            
    return f"Timeout: Teammate '{name}' did not respond within 60 seconds."


# === Tool Schema and Dispatch Extensions ===

TEAM_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "send_to_teammate",
        "description": "Delegate a subtask to a specialist teammate. This blocks until they reply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "enum": list(TEAMMATES.keys())},
                "message": {"type": "string", "description": "Specific tasks/instructions."},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "list_teammates",
        "description": "List all currently available specialist agents and their roles.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TEAM_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Include standard bash, read, etc.
    "send_to_teammate": lambda inp: run_send_to_teammate(inp["name"], inp["message"]),
    "list_teammates":   lambda inp: "\n".join(f"  - {n}: {s[:80]}..." for n, s in TEAMMATES.items()),
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction and manages the background team lifecycle.
    """
    # 1. Initialize Thread Control
    stop_signal = threading.Event()
    teammate_threads: List[threading.Thread] = []

    # 2. Spawn Teammate Background Threads
    for agent_name, agent_prompt in TEAMMATES.items():
        thread = threading.Thread(
            target=_run_teammate_loop, 
            args=(agent_name, agent_prompt, stop_signal),
            daemon=True # Ensures threads exit when the main program closes
        )
        thread.start()
        teammate_threads.append(thread)

    # UI Header in Gray
    print(f"\033[90ms09: agent teams | teammates: {', '.join(TEAMMATES)} | mailboxes in {MAILBOX_DIR}\033[0m\n")
    
    # 3. Main Lead Agent REPL
    history: List[Dict[str, Any]] = []
    
    try:
        while True:
            try:
                # User Prompt in Cyan
                query: str = input("\033[36ms09 >> \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                # Graceful termination on Ctrl+C/D
                print("\nExiting session.")
                break

            # Exit check
            if not query or query.lower() in ("q", "exit", "quit"):
                break

            # Record and process user query
            history.append({"role": "user", "content": query})
            stream_loop(
                messages=history,
                tools=TEAM_TOOLS,
                dispatch=TEAM_DISPATCH,
                system=SYSTEM
            )
            print()

    finally:
        # 4. Clean Shutdown: Signal threads to stop and clear mailboxes
        print("\033[90m  [system] shutting down team...\033[0m")
        stop_signal.set()
        # Optionally cleanup mailbox files
        for agent in list(TEAMMATES.keys()) + ["lead"]:
            path = _get_mailbox_path(agent)
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    # Script entry point
    main()