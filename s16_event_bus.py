#!/usr/bin/env python3
"""
s16_event_bus.py: Implementation of an Event-Driven Hook System for AI Agents.

Motto: "Every action fires an event; hooks let you observe and intercept"

This module introduces a "Middleware" architecture for AI agents. By utilizing 
an Event Bus, developers can attach custom logic (hooks) to various stages 
of the agent's lifecycle. This is essential for observability, auditing, 
performance tracking, and safety.

Key Architectural Concepts:
    1. The Event Bus: A centralized registry (Observer Pattern) that dispatches 
       payloads to registered handlers when specific events occur.
    2. Lifecycle Hooks: Events are fired at critical moments: session start/end, 
       pre/post tool execution, and model responses.
    3. Interception: 'pre_tool_use' hooks can return a 'block' signal, 
       allowing external logic to prevent a tool from running dynamically.
    4. Decoupling: Logging, statistics, and timing logic are moved out of 
       the main agent loop and into independent, swappable functions.

Supported Events:
    - session_start: Triggered when the agent loop begins.
    - agent_response: Triggered when the LLM finishes a text turn.
    - pre_tool_use: Triggered BEFORE a tool is executed.
    - post_tool_use: Triggered AFTER a tool successfully finishes.
    - tool_error: Triggered if a tool execution crashes.
    - session_end: Triggered when the agent loop terminates (even on error).
"""

# === Standard Library Imports ===
import os                          # Operating system interfaces
import sys                         # System-specific parameters and functions
from collections import defaultdict # Dictionary that provides default values for keys
from datetime import datetime       # Manipulating dates and times
from typing import List, Dict, Any, Callable, Optional, Union # For strict type hinting

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # Model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    load_rules,        # YAML rule loader
    check_permission,  # Permission logic
    stream_loop        # Reference loop (not used directly here)
)

# === Event Bus Implementation ===

class EventBus:
    """
    A simple Pub/Sub event system to manage lifecycle hooks.
    """

    def __init__(self) -> None:
        """Initializes the handler registry using a default dictionary of lists."""
        # Maps event names (str) to lists of callback functions (Callable)
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable) -> "EventBus":
        """
        Registers a callback function for a specific event.

        Args:
            event (str): The name of the event to listen for.
            handler (Callable): The function to call when the event fires.

        Returns:
            EventBus: Returns self to allow method chaining.
        """
        self._handlers[event].append(handler)
        return self

    def emit(self, event: str, **payload: Any) -> List[Any]:
        """
        Triggers an event and executes all registered handlers.

        Args:
            event (str): The name of the event to trigger.
            **payload: Arbitrary keyword arguments to pass to the handlers.

        Returns:
            List[Any]: A list of return values from all executed handlers.
        """
        results = []
        # Iterate through every handler registered for this specific event
        for handler in self._handlers[event]:
            try:
                # Execute the handler and capture its return value
                result = handler(event=event, **payload)
                if result is not None:
                    results.append(result)
            except Exception as e:
                # Catch and log hook errors so they don't crash the main agent
                print(f"\033[31m[EventBus] Hook error on '{event}': {e}\033[0m")
        return results


# Global instance of the bus for application-wide use
bus = EventBus()

# === Built-in Hook Implementations ===

# Path to the persistent event log
_LOG_FILE: str = ".agent_events.log"

def hook_logger(event: str, **payload: Any) -> None:
    """Writes a timestamped record of the event to a log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tool_name = payload.get("tool", "N/A")
    # Construct a log line summarizing the activity
    log_line = f"[{timestamp}] EVENT={event} TOOL={tool_name}"
    
    # Append to the log file
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")


def hook_stats(event: str, **payload: Any) -> None:
    """Tracks frequency of tool usage across the session."""
    # We use function attributes to maintain state between calls
    if not hasattr(hook_stats, "_counts"):
        hook_stats._counts = defaultdict(int)

    if event == "session_start":
        # Reset counters at the start of a new session
        hook_stats._counts.clear()
    elif event == "post_tool_use":
        # Increment the specific tool's counter
        tool = payload.get("tool", "unknown")
        hook_stats._counts[tool] += 1
    elif event == "session_end":
        # Print a final summary of activity in Gray
        if hook_stats._counts:
            print(f"\033[90m  [stats] Tool Usage: {dict(hook_stats._counts)}\033[0m")


def hook_timer(event: str, **payload: Any) -> None:
    """Measures and logs tool execution time."""
    if not hasattr(hook_timer, "_start_times"):
        hook_timer._start_times = {}

    if event == "pre_tool_use":
        # Record start time using the tool name as a key
        hook_timer._start_times[payload.get("tool")] = datetime.now()
    elif event == "post_tool_use":
        tool_name = payload.get("tool")
        start_time = hook_timer._start_times.pop(tool_name, None)
        if start_time:
            # Calculate duration in seconds
            duration = (datetime.now() - start_time).total_seconds()
            # Only alert the user if a command is significantly slow (> 5s)
            if duration > 5.0:
                print(f"\033[90m  [timer] Warning: '{tool_name}' was slow ({duration:.1f}s)\033[0m")


# Register the built-in hooks to the bus
bus.on("pre_tool_use",  hook_logger).on("post_tool_use", hook_logger)
bus.on("session_start", hook_stats).on("post_tool_use", hook_stats).on("session_end", hook_stats)
bus.on("pre_tool_use",  hook_timer).on("post_tool_use", hook_timer)


# === Event-Aware Agent Loop ===

def agent_loop_with_hooks(messages: List[Dict[str, Any]]) -> None:
    """
    An autonomous agent loop that emits events at every lifecycle stage.

    Args:
        messages (List[Dict[str, Any]]): The session's conversation history.
    """
    # Signal that a new interaction session has begun
    bus.emit("session_start")
    
    try:
        while True:
            # Indicate thinking phase
            print("\n\033[36m> Thinking...\033[0m")
            
            # Start streaming from Anthropic API
            with client.messages.stream(
                model=MODEL, 
                system=f"You are a coding agent at {os.getcwd()}.",
                messages=messages, 
                tools=EXTENDED_TOOLS, 
                max_tokens=8000,
            ) as stream:
                text_chunks = []
                for text in stream.text_stream:
                    # Live print to terminal
                    print(text, end="", flush=True)
                    text_chunks.append(text)
                
                # Assemble the final response object
                response = stream.get_final_message()
            
            print() # Visual newline after response
            
            # If the model produced text, fire the agent_response event
            if text_chunks:
                bus.emit("agent_response", text="".join(text_chunks))
            
            # Update history
            messages.append({"role": "assistant", "content": response.content})
            
            # Terminate loop if the model didn't call any tools
            if response.stop_reason != "tool_use":
                break

            # Process tool blocks
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                
                # 1. Fire 'pre_tool_use' and check for blocks
                # Hooks can return {"block": True} to intercept execution
                pre_results = bus.emit("pre_tool_use", tool=block.name, input=block.input)
                is_blocked = any(r.get("block") for r in pre_results if isinstance(r, dict))
                
                if is_blocked:
                    output = "Error: Execution blocked by system security hook."
                else:
                    # UI feedback in Yellow
                    display_input = str(list(block.input.values())[0])[:80]
                    print(f"\033[33m[{block.name}] {display_input}...\033[0m")
                    
                    # Look up and execute the tool handler
                    handler = EXTENDED_DISPATCH.get(block.name)
                    try:
                        if handler:
                            output = handler(block.input)
                            # 2. Fire 'post_tool_use' on success
                            bus.emit("post_tool_use", tool=block.name, input=block.input, output=output)
                        else:
                            output = f"Error: Unknown tool '{block.name}'"
                    except Exception as e:
                        output = f"Execution Error: {e}"
                        # 3. Fire 'tool_error' on failure
                        bus.emit("tool_error", tool=block.name, error=str(e))
                
                # Show snippet of output
                print(str(output)[:300])
                
                # Collect results for model history
                tool_results.append({
                    "type": "tool_result", 
                    "tool_use_id": block.id, 
                    "content": str(output)
                })
            
            # Feed results back into the history for the next model turn
            messages.append({"role": "user", "content": tool_results})
            
    finally:
        # Signal that the entire multi-turn interaction has concluded
        # This runs even if a KeyboardInterrupt or error occurs
        bus.emit("session_end")


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s16 'Event-Driven' agent.
    """
    # UI Header
    print(f"\033[90ms16: event bus | hooks: logger, stats, timer | log → {_LOG_FILE}\033[0m\n")
    print("\033[90m  Note: Hooks are running in the background. Check logs for details.\033[0m\n")
    
    # Session interaction history
    history: List[Dict[str, Any]] = []

    # Main REPL loop
    while True:
        try:
            # User Prompt in Cyan
            query: str = input("\033[36ms16 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit
            print("\nExiting session.")
            sys.exit(0)

        # Exit handler
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query
        history.append({"role": "user", "content": query})
        
        # Trigger the hook-aware autonomous agent loop
        agent_loop_with_hooks(history)
        
        # Formatting spacer
        print()


if __name__ == "__main__":
    # Script entry point
    main()