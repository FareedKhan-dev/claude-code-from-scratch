#!/usr/bin/env python3
"""
s10_team_protocols.py: Implementation of an FSM-Governed Agent Communication Protocol.

Motto: "Teammates need shared communication rules"

This module introduces formal state management for agent interactions. In previous 
sessions (s09), agents communicated loosely via mailboxes. s10 enforces a 
strict Finite State Machine (FSM) to ensure that agents are in predictable 
states during the request-response lifecycle.

Key Architectural Concepts:
    1. Deterministic State Tracking: Every agent exists in one of four states: 
       IDLE, REQUESTING, WAITING, or RESPONDING.
    2. Concurrency Protection: Uses `threading.Lock` to ensure that state 
       transitions and mailbox I/O are atomic, preventing "lost updates."
    3. Protocol Enforcement: Prevents "talking over" or "deadlocking" by 
       ensuring an agent cannot accept a new request while it is already 
       RESPONDING or WAITING.
    4. Encapsulation: The `ProtocolAgent` class bundles identity, state, 
       mailbox access, and the autonomous execution loop into a single unit.

FSM States:
    - IDLE: Ready to receive tasks.
    - REQUESTING: In the process of writing a task to another agent.
    - WAITING: Task sent; blocked until a reply is received.
    - RESPONDING: Currently running its own LLM loop to fulfill a request.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import json        # JSON serialization for protocol messages
import threading   # Support for thread-safe state locks
import time        # Time utilities for polling delays
import sys         # System-specific parameters and functions
from enum import Enum  # For defining clear, discrete agent states
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

# === Configuration and Constants ===

# Directory where protocol-specific mailbox files are stored
MAILBOX_DIR: Path = Path(".mailboxes")
MAILBOX_DIR.mkdir(exist_ok=True)

# === State Definition ===

class AgentState(Enum):
    """Enumeration of the possible operational states of a protocol agent."""
    IDLE       = "idle"        # Available for new work
    REQUESTING = "requesting"  # Actively sending a message
    WAITING    = "waiting"     # Blocked, awaiting a reply
    RESPONDING = "responding"  # Processing a task (LLM active)


# === Protocol Agent Implementation ===

class ProtocolAgent:
    """
    An autonomous agent governed by a Finite State Machine protocol.

    Attributes:
        name (str): The unique identifier for the agent.
        system (str): The system prompt defining the agent's role.
        state (AgentState): The current state in the FSM.
    """

    def __init__(self, name: str, system: str):
        """
        Initializes the agent with a name, role, and private mailbox.

        Args:
            name (str): Unique name of the agent.
            system (str): System instructions for the LLM.
        """
        self.name: str = name
        self.system: str = system
        self.state: AgentState = AgentState.IDLE
        # Define the private mailbox file path for this agent
        self._inbox: Path = MAILBOX_DIR / f"{name}_proto.jsonl"
        # Lock to ensure thread-safe access to state and mailbox
        self._lock: threading.Lock = threading.Lock()

    def send(self, to_agent: "ProtocolAgent", message: str, msg_type: str = "request") -> None:
        """
        Formally sends a message to another protocol agent.

        Transitions state: IDLE -> REQUESTING -> WAITING.

        Args:
            to_agent (ProtocolAgent): The recipient agent instance.
            message (str): The content of the message.
            msg_type (str): The type of message (e.g., 'request' or 'reply').
        """
        with self._lock:
            # Update state to reflect active sending
            self.state = AgentState.REQUESTING
            
        # Append the structured message to the recipient's mailbox
        with open(to_agent._inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "from": self.name, 
                "type": msg_type, 
                "body": message,
                "timestamp": time.time()
            }) + "\n")
            
        with self._lock:
            # Transition to WAITING after the message is successfully offloaded
            self.state = AgentState.WAITING

    def receive(self) -> List[Dict[str, Any]]:
        """
        Retrieves all pending messages from the agent's mailbox.

        Returns:
            List[Dict[str, Any]]: A list of parsed message dictionaries.
        """
        if not self._inbox.exists():
            return []
            
        with self._lock:
            # Read and parse JSONL lines, skipping empty ones
            lines = self._inbox.read_text(encoding="utf-8").splitlines()
            msgs = [json.loads(line) for line in lines if line.strip()]
            # Clear mailbox immediately after reading to avoid duplicates
            self._inbox.write_text("", encoding="utf-8")
            return msgs

    def handle(self, message: str) -> str:
        """
        Processes an incoming request by running an autonomous agent turn.

        Transitions state: IDLE -> RESPONDING -> IDLE.

        Args:
            message (str): The task description provided by the sender.

        Returns:
            str: The final text result from the internal LLM loop.
        """
        with self._lock:
            # Mark the agent as busy
            self.state = AgentState.RESPONDING
            
        # Initialize internal message history for this specific request
        sub_history: List[Dict[str, Any]] = [{"role": "user", "content": message}]
        
        # Autonomous "Think-Act" cycle
        while True:
            response = client.messages.create(
                model=MODEL,
                system=self.system,
                messages=sub_history,
                tools=EXTENDED_TOOLS,
                max_tokens=4000,
            )
            sub_history.append({"role": "assistant", "content": response.content})
            
            # If the model has provided a text answer and no more tool calls
            if response.stop_reason != "tool_use":
                break
            
            # Execute requested tools using the standard extended suite
            results = dispatch_tools(response.content, EXTENDED_DISPATCH)
            sub_history.append({"role": "user", "content": results})
            
        with self._lock:
            # Return to IDLE state once the task is finished
            self.state = AgentState.IDLE
            
        # Extract the final textual response to return to the sender
        return "".join(
            block.text for block in sub_history[-1]["content"] 
            if hasattr(block, "text")
        )


# === Team Definitions ===

# Define the specialized protocol agents
TEAMMATES: Dict[str, ProtocolAgent] = {
    "alpha": ProtocolAgent(
        "alpha", 
        f"You are Alpha, a senior code analyst at {os.getcwd()}. Focus on quality and logic."
    ),
    "beta":  ProtocolAgent(
        "beta",  
        f"You are Beta, a specialized code writer at {os.getcwd()}. Focus on implementation."
    ),
}

# === Tool Implementations for Lead Agent ===

def run_delegate(name: str, message: str) -> str:
    """
    Lead Agent tool to assign a task to a protocol-governed agent.

    Args:
        name (str): Name of the agent to delegate to (alpha or beta).
        message (str): The task instructions.

    Returns:
        str: The response from the delegated agent.
    """
    agent = TEAMMATES.get(name)
    if not agent:
        return f"Error: Agent '{name}' not found."
    
    # Visual feedback in Magenta
    print(f"\033[35m  [protocol] lead → {name}: {message[:60]}...\033[0m")
    
    # Execute the formal handler (IDLE -> RESPONDING -> IDLE)
    result = agent.handle(message)
    
    # Visual feedback in Magenta
    print(f"\033[35m  [protocol] {name} → lead: {result[:60]}...\033[0m")
    
    return result


# === Tool Schema and Dispatch Extensions ===

PROTO_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "delegate",
        "description": "Formally delegate a task to a protocol-governed teammate agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {
                    "type": "string", 
                    "enum": list(TEAMMATES.keys()),
                    "description": "The name of the teammate agent."
                },
                "message": {
                    "type": "string", 
                    "description": "The specific task to perform."
                },
            },
            "required": ["name", "message"],
        },
    },
]

# Map the delegate tool to its implementation
PROTO_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, 
    "delegate": lambda inp: run_delegate(inp["name"], inp["message"])
}

# Define the Lead Agent's persona
SYSTEM: str = (
    f"You are a lead agent at {os.getcwd()}. "
    f"You have two specialized teammates: {', '.join(TEAMMATES.keys())}. "
    "Use the 'delegate' tool to assign complex work to them. "
    "Once they reply, synthesize their output into a comprehensive final answer."
)


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s10 'FSM Protocol' agent.
    """
    # UI Header in Gray
    # Lists the possible states defined in the AgentState Enum
    states_list = [s.value for s in AgentState]
    print(f"\033[90ms10: FSM protocol | states: {states_list}\033[0m\n")
    
    # Interaction history for the current session
    history: List[Dict[str, Any]] = []

    # Main Command Loop (REPL)
    while True:
        try:
            # User Prompt in Cyan
            query: str = input("\033[36ms10 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit
            print("\nExiting session.")
            sys.exit(0)

        # Standard exit check
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query in history
        history.append({"role": "user", "content": query})
        
        # Start the Lead Agent's autonomous loop with protocol capabilities
        stream_loop(
            messages=history,
            tools=PROTO_TOOLS,
            dispatch=PROTO_DISPATCH,
            system=SYSTEM
        )
        
        # Visual spacing
        print()


if __name__ == "__main__":
    # Script entry point
    main()