#!/usr/bin/env python3
"""
s20_cache_optimization.py: Implementation of Anthropic Prompt Caching (KV Cache).

Motto: "Never rebuild what you've already sent"

This module demonstrates how to utilize Anthropic's Prompt Caching feature. 
Caching allows the API to store a 'prefix' of the conversation (System prompt, 
tools, and early history) on their servers. When a 'Cache Hit' occurs, 
the model processes the input significantly faster and at a fraction of 
the normal input token cost.

Key Architectural Concepts:
    1. Cache Control Blocks: The system prompt is structured as a list of 
       content blocks, with a `cache_control` flag on the final block.
    2. Tool Definition Caching: By adding a `cache_control` marker to the 
       last entry in the tools array, the entire toolset is cached.
    3. Ephemeral Caching: Markers use the 'ephemeral' type, which typically 
       lives for 5 minutes and is refreshed on every hit.
    4. Usage Monitoring: Accessing `response.usage` allows us to calculate 
       Cache Hits (cheap) vs. Cache Misses (expensive).

Operational Benefits:
    - Reduced Latency: Faster Time To First Token (TTFT).
    - Reduced Cost: Input tokens read from cache are billed at a ~90% discount.
"""

# === Standard Library Imports ===
import asyncio      # Asynchronous I/O and task management
import os          # Operating system interfaces
import copy        # Shallow and deep copying operations
import sys         # System-specific parameters and functions
from typing import List, Dict, Any, Tuple, Optional, Union # For strict type hinting

# === Local Module Imports ===
# We leverage the core's async capabilities and standard toolsets.
from core import (
    client,            # The Anthropic API client
    MODEL,             # The specific model ID
    EXTENDED_TOOLS,    # Standard file and shell tools
    ASYNC_DISPATCH     # Dictionary mapping tool names to async handlers
)

# === Cacheable Configuration ===

# Anthropic requires the system prompt to be a list of blocks for caching.
# We mark the end of the system block to ensure instructions are cached.
CACHED_SYSTEM: List[Dict[str, Any]] = [
    {
        "type": "text",
        "text": (
            f"You are a coding agent at {os.getcwd()}. "
            "Use tools to solve tasks. Be concise and precise.\n\n"
            "Tools available: bash, read, write, grep, glob, revert.\n"
            "Always read before writing. Always check your work."
        ),
        # This marker tells the API to cache everything up to this point
        "cache_control": {"type": "ephemeral"},
    }
]

# Clone the existing tools array to avoid mutating the core definition.
CACHED_TOOLS: List[Dict[str, Any]] = copy.deepcopy(EXTENDED_TOOLS)

# Add the cache_control marker to the VERY LAST tool in the list.
# Anthropic caches everything in the prompt UP TO the last marker.
CACHED_TOOLS[-1]["cache_control"] = {"type": "ephemeral"}


# === Usage and Cache Statistics Tracking ===

class CacheStats:
    """
    Tracks and summarizes API usage with a focus on Cache Hits vs. Misses.
    """

    def __init__(self) -> None:
        """Initializes counters for various token types and call frequency."""
        self.created: int = 0   # Tokens written to cache (Cache Miss)
        self.read: int = 0      # Tokens read from cache (Cache Hit)
        self.uncached: int = 0  # Tokens processed without cache
        self.calls: int = 0     # Total number of API turns

    def record(self, usage: Any) -> None:
        """
        Extracts usage metadata from an Anthropic Message object.

        Args:
            usage (Any): The usage attribute from the API response.
        """
        self.calls += 1
        # Extract specific cache-related token counts from the response usage
        self.created += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.uncached += getattr(usage, "input_tokens", 0) or 0

    def show_turn(self, usage: Any) -> None:
        """
        Prints immediate feedback regarding the cache state of the current turn.

        Args:
            usage (Any): The usage attribute from the current API response.
        """
        created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        
        if created > 0:
            # A Cache Miss occurred; we paid to write this context into the cache
            print(f"\033[90m  [cache] MISS → {created} tokens written to cache\033[0m")
        elif read > 0:
            # A Cache Hit occurred; we saved ~90% on these tokens
            saved = int(read * 0.9)
            print(f"\033[90m  [cache] HIT  → {read} tokens read (saved ≈{saved} tokens)\033[0m")

    def summary(self) -> None:
        """Prints a global summary of savings at the end of the session."""
        if self.calls > 0:
            total_saved = int(self.read * 0.9)
            print(f"\n\033[90m  [cache total] turns={self.calls} | written={self.created} "
                  f"| hits={self.read} | estimated savings={total_saved} tokens\033[0m")


# Initialize global stats tracker
stats = CacheStats()


# === Asynchronous Tool Dispatching ===

async def dispatch_one_tool(block: Any) -> Tuple[str, str]:
    """
    Executes a single tool call asynchronously with logging.

    Args:
        block (Any): The tool_use block from the model response.

    Returns:
        Tuple[str, str]: (tool_use_id, execution_output)
    """
    tool_input = block.input
    tool_name = block.name
    
    # Retrieve the async handler from core
    handler = ASYNC_DISPATCH.get(tool_name)
    
    # UI Feedback: Log tool execution in Yellow
    first_val = str(list(tool_input.values())[0])[:80] if tool_input else ""
    print(f"\033[33m[{tool_name}] {first_val}...\033[0m")
    
    try:
        # Execute the handler and await its result
        output = await handler(tool_input) if handler else f"Error: Unknown tool {tool_name}"
    except Exception as e:
        output = f"Execution Error: {e}"
        
    # Print a snippet of the tool output
    print(str(output)[:200])
    
    return block.id, str(output)


# === Agent Loop with Caching ===

async def agent_loop_cached(messages: List[Dict[str, Any]]) -> None:
    """
    The Thinking-Acting loop optimized for KV Cache hits.

    Args:
        messages (List[Dict[str, Any]]): The active conversation history.
    """
    while True:
        print("\n\033[36m> Thinking...\033[0m")

        def _blocking_stream_call():
            """Runs the synchronous SDK stream in a background thread."""
            with client.messages.stream(
                model=MODEL,
                system=CACHED_SYSTEM,   # Provide the list of blocks with cache_control
                messages=messages,
                tools=CACHED_TOOLS,     # Provide the tools with the last-tool marker
                max_tokens=8000,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                return stream.get_final_message()

        # Execute the thinking turn in a thread to keep the event loop free
        response = await asyncio.get_event_loop().run_in_executor(None, _blocking_stream_call)
        print() # Newline after response text

        # Record and display cache statistics if available in the response metadata
        if hasattr(response, "usage"):
            stats.record(response.usage)
            stats.show_turn(response.usage)

        # Update the context history
        messages.append({"role": "assistant", "content": response.content})

        # Exit if the model has provided a final answer
        if response.stop_reason != "tool_use":
            return

        # Parallel Tool Execution
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        execution_pairs = await asyncio.gather(*[dispatch_one_tool(b) for b in tool_blocks])
        
        # Map results back to the tool use IDs
        results_map = dict(execution_pairs)
        turn_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": results_map[b.id]}
            for b in tool_blocks
        ]
        
        # Append tool results to history for the next model turn
        messages.append({"role": "user", "content": turn_results})


# === Main Execution Entry Point ===

async def main() -> None:
    """
    Initializes the terminal interaction and manages the session lifecycle.
    """
    # UI Header
    print("\033[90ms20: prompt caching | system + tools marked cacheable | [cache] HIT/MISS per turn\033[0m\n")
    
    # Initialize session history
    history: List[Dict[str, Any]] = []
    
    loop = asyncio.get_event_loop()
    
    try:
        # Main Command Loop
        while True:
            try:
                # Capture input in a non-blocking thread
                query: str = await loop.run_in_executor(
                    None, lambda: input("\033[36ms20 >> \033[0m").strip()
                )
            except (EOFError, KeyboardInterrupt):
                # Graceful handling of Ctrl+C / Ctrl+D
                break

            # Exit check
            if not query or query.lower() in ("q", "exit", "quit"):
                break

            # Record user query
            history.append({"role": "user", "content": query})
            
            # Start the cached agent interaction
            await agent_loop_cached(history)
            
            # Visual separator
            print()
            
    finally:
        # Print total savings before the program closes
        stats.summary()


if __name__ == "__main__":
    # Start the asynchronous event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass