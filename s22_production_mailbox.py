#!/usr/bin/env python3
"""
s22_production_mailbox.py: Implementation of Redis-Based Agent Messaging.

Motto: "Redis pipes the messages; no JSONL file left waiting"

This module represents the production-tier evolution of the agent team 
architecture. In session s09, agents communicated via JSONL files on disk, 
which required constant filesystem polling and file locking. s22 replaces this 
with Redis Pub/Sub, providing a real-time event-driven messaging layer.

Key Architectural Concepts:
    1. Interface Abstraction: The `MailboxBackend` base class allows the agent 
       logic to remain identical regardless of whether we use Redis (Production) 
       or asyncio.Queue (Development/Fallback).
    2. Pub/Sub Pattern: Agents "Subscribe" to their own name-spaced channels 
       and "Publish" to others, enabling instant message delivery without polling.
    3. Concurrency: Built entirely on `asyncio`, allowing the Lead agent and 
       multiple teammates to handle I/O and thinking turns in parallel.
    4. Decoupling: This architecture allows agents to run on entirely 
       different servers as long as they can connect to the same Redis instance.

Requirements:
    - pip install redis
    - A running Redis server (e.g., `docker run -p 6379:6379 redis`)
"""

# === Standard Library Imports ===
import asyncio      # Asynchronous I/O and concurrency
import json         # Message serialization/deserialization
import os           # Operating system interfaces for environment variables
import sys          # System-specific parameters
from abc import ABC, abstractmethod  # Abstract Base Classes for interfaces
from datetime import datetime        # Timestamping messages
from typing import List, Dict, Any, Optional, Union # For strict type hinting

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    async_bash         # Asynchronous shell tool from core.py
)

# === Optional Dependency: Redis ===
try:
    # Attempt to import the asynchronous Redis client
    import redis.asyncio as aioredis
    HAS_REDIS: bool = True
except ImportError:
    # Fallback to local memory if Redis library is missing
    HAS_REDIS = False
    print("\033[33mWarning: 'redis' package not found. Run: pip install redis\033[0m")
    print("\033[33mFalling back to in-memory asyncio.Queue.\033[0m\n")

# Configuration for Redis connection
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")


# === Mailbox Interface & Implementations ===

class MailboxBackend(ABC):
    """
    Abstract Base Class defining the contract for agent communication.
    """

    @abstractmethod
    async def send(self, to_agent: str, message: Dict[str, Any]) -> None:
        """Sends a message to a specific agent's inbox."""
        pass

    @abstractmethod
    async def receive(self, agent_name: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """Awaits and retrieves a message from the agent's inbox."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cleans up backend resources (connections, threads, etc.)."""
        pass


class RedisMailbox(MailboxBackend):
    """
    Production implementation of the mailbox using Redis Pub/Sub channels.
    """

    def __init__(self, url: str):
        """Initializes the Redis connection."""
        # Create a Redis client with auto-decoding for string data
        self._redis = aioredis.from_url(url, decode_responses=True)
        # Store active PubSub subscription objects to prevent redundant subscriptions
        self._pubsubs: Dict[str, Any] = {}

    def _get_channel_name(self, agent_name: str) -> str:
        """Generates a Redis channel key for a given agent name."""
        return f"agent:{agent_name}:inbox"

    async def send(self, to_agent: str, message: Dict[str, Any]) -> None:
        """Publishes a JSON-encoded message to the recipient's Redis channel."""
        channel = self._get_channel_name(to_agent)
        # Add a standardized timestamp to every message
        payload = {**message, "timestamp": datetime.now().isoformat()}
        # Publish to Redis
        await self._redis.publish(channel, json.dumps(payload))

    async def receive(self, agent_name: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """
        Listens to the agent's channel for an incoming message.
        """
        channel = self._get_channel_name(agent_name)
        
        # Initialize PubSub subscription if this is the first time checking this inbox
        if agent_name not in self._pubsubs:
            ps = self._redis.pubsub()
            await ps.subscribe(channel)
            self._pubsubs[agent_name] = ps
            
        ps = self._pubsubs[agent_name]
        deadline = asyncio.get_event_loop().time() + timeout
        
        # Loop until a message is received or the timeout is reached
        while asyncio.get_event_loop().time() < deadline:
            # Check for a message without blocking the entire loop
            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg["type"] == "message":
                try:
                    # Parse the JSON payload
                    return json.loads(msg["data"])
                except json.JSONDecodeError:
                    # Fallback if data is raw text
                    return {"body": msg["data"]}
            # Small sleep to prevent high CPU usage during idle polling
            await asyncio.sleep(0.05)
            
        return None # Return None if no message arrived within the timeout

    async def close(self) -> None:
        """Gracefully shuts down all Redis subscriptions and connections."""
        for ps in self._pubsubs.values():
            await ps.unsubscribe()
            await ps.close()
        await self._redis.aclose()


class QueueMailbox(MailboxBackend):
    """
    Development fallback implementation using local in-memory asyncio queues.
    """

    def __init__(self) -> None:
        """Initializes a registry of queues."""
        self._queues: Dict[str, asyncio.Queue] = {}

    def _get_queue(self, agent_name: str) -> asyncio.Queue:
        """Ensures a queue exists for the given agent and returns it."""
        if agent_name not in self._queues:
            self._queues[agent_name] = asyncio.Queue()
        return self._queues[agent_name]

    async def send(self, to_agent: str, message: Dict[str, Any]) -> None:
        """Puts a message into the recipient's local queue."""
        payload = {**message, "timestamp": datetime.now().isoformat()}
        await self._get_queue(to_agent).put(payload)

    async def receive(self, agent_name: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """Awaits a message from the local queue with a timeout."""
        try:
            return await asyncio.wait_for(self._get_queue(agent_name).get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        """No cleanup needed for in-memory queues."""
        pass


async def initialize_mailbox() -> MailboxBackend:
    """
    Factory function to create the best available mailbox backend.
    """
    if HAS_REDIS:
        try:
            mb = RedisMailbox(REDIS_URL)
            # Test the connection with a PING
            await mb._redis.ping()
            print(f"\033[90m  [mailbox] Successfully connected to Redis at {REDIS_URL}\033[0m")
            return mb
        except Exception as e:
            print(f"\033[33m  [mailbox] Redis unavailable ({e}). Falling back to Queue.\033[0m")
            
    return QueueMailbox()


# === Teammate (Worker) Agent Logic ===

async def teammate_worker_loop(name: str, system_prompt: str, mailbox: MailboxBackend, stop_signal: asyncio.Event) -> None:
    """
    The background loop for a specialist agent worker.

    Args:
        name (str): The name of the agent.
        system_prompt (str): Defining the agent's role/specialty.
        mailbox (MailboxBackend): The shared messaging system.
        stop_signal (asyncio.Event): Signal to stop the background thread.
    """
    print(f"\033[90m  [{name}] worker online and ready\033[0m")
    
    while not stop_signal.is_set():
        # Await a new task from the Lead
        msg = await mailbox.receive(name, timeout=2.0)
        if not msg:
            continue
            
        sender = msg.get("from", "lead")
        task_description = msg.get("body", "")
        
        print(f"\n\033[35m  [{name}] Task received from {sender}: {task_description[:60]}...\033[0m")
        
        # Initialize context for this specific task
        history: List[Dict[str, Any]] = [{"role": "user", "content": task_description}]
        
        # Autonomous Thought/Action cycle
        while True:
            # We run the SDK call in a thread to avoid blocking the async loop
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.messages.create(
                    model=MODEL, 
                    system=system_prompt, 
                    messages=history,
                    tools=EXTENDED_TOOLS, 
                    max_tokens=4000,
                )
            )
            history.append({"role": "assistant", "content": response.content})
            
            # If the model is done, exit the action loop
            if response.stop_reason != "tool_use":
                break
                
            # Execute tools (Worker agents in s22 focus on bash-based actions)
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    # Use the async_bash tool provided by core.py
                    cmd_output = await async_bash(block.input.get("command", ""))
                    tool_results.append({
                        "type": "tool_result", 
                        "tool_use_id": block.id, 
                        "content": cmd_output
                    })
            history.append({"role": "user", "content": tool_results})
            
        # Extract textual conclusion and send it back to the sender's mailbox
        final_answer = "".join(b.text for b in history[-1]["content"] if hasattr(b, "text"))
        
        await mailbox.send(sender, {
            "from": name, 
            "to": sender, 
            "type": "result", 
            "body": final_answer
        })
        print(f"\033[35m  [{name}] Work completed. Result sent back to {sender}.\033[0m")


# === Lead (Manager) Agent Logic ===

async def lead_orchestration_loop(user_query: str, mailbox: MailboxBackend, teammate_names: List[str]) -> str:
    """
    Handles the fan-out and fan-in of tasks to the team.

    Args:
        user_query (str): The original user prompt.
        mailbox (MailboxBackend): Messaging layer.
        teammate_names (List[str]): List of available agents.

    Returns:
        str: A synthesized result of all teammate contributions.
    """
    # 1. FAN-OUT: Send the sub-task to every specialist agent simultaneously
    for i, name in enumerate(teammate_names):
        await mailbox.send(name, {
            "from": "lead", 
            "to": name, 
            "type": "request",
            "body": f"Please contribute to this request from your specialty: {user_query}"
        })
        
    # 2. FAN-IN: Collect replies from the mailbox
    collected_results: Dict[str, str] = {}
    print(f"\033[90m  [lead] Waiting for {len(teammate_names)} replies...\033[0m")
    
    for _ in range(len(teammate_names)):
        # Wait up to 60 seconds for each specialist to finish
        msg = await mailbox.receive("lead", timeout=60.0)
        if msg:
            worker_name = msg.get("from", "unknown")
            collected_results[worker_name] = msg.get("body", "")
            
    # 3. SYNTHESIS: Combine worker outputs into a single string
    if not collected_results:
        return "System Error: No specialists responded within the timeout."
        
    return "\n\n".join(f"### Report from [{name}]:\n{body}" for name, body in collected_results.items())


# === Main Application Runtime ===

async def main() -> None:
    """
    Initializes the mailbox, spawns the team, and manages the main REPL.
    """
    print("\033[90ms22: Redis production mailboxes | upgrading s09 agent teams\033[0m")
    
    # 1. Setup Messaging Backend
    mailbox = await initialize_mailbox()
    
    # 2. Define Team Structure
    TEAM_DEF: Dict[str, str] = {
        "alpha": f"You are Alpha, a senior code analyst at {os.getcwd()}. Focus on quality.",
        "beta":  f"You are Beta, a specialized implementation agent at {os.getcwd()}. Focus on speed.",
    }
    
    # 3. Spawn Worker Tasks
    stop_event = asyncio.Event()
    worker_tasks = [
        asyncio.create_task(teammate_worker_loop(name, prompt, mailbox, stop_event))
        for name, prompt in TEAM_DEF.items()
    ]
    
    print(f"  Team active: {', '.join(TEAM_DEF.keys())} | Protocol: {'Redis Pub/Sub' if HAS_REDIS else 'Local Async Queue'}\n")
    
    # 4. Main Lead Interaction REPL
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                # Capture input asynchronously
                query = await loop.run_in_executor(None, lambda: input("\033[36ms22 >> \033[0m").strip())
            except (EOFError, KeyboardInterrupt):
                break
                
            if not query or query.lower() in ("q", "exit", "quit"):
                break
                
            # Trigger the lead orchestration turn
            # In s22, the lead is a logic-only function that delegates to workers
            synthesized_report = await lead_orchestration_loop(query, mailbox, list(TEAM_DEF.keys()))
            
            # Print final findings
            print(f"\n\033[36m[lead] Final Synthesized Report:\033[0m\n{synthesized_report}\n")
            
    finally:
        # 5. Graceful Cleanup
        print("\033[90m  [system] Shutting down team and closing mailbox connections...\033[0m")
        stop_event.set()
        # Cancel all background worker tasks
        for t in worker_tasks:
            t.cancel()
        # Close Redis connection or clear local queues
        await mailbox.close()


if __name__ == "__main__":
    # Launch the asyncio entry point
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass