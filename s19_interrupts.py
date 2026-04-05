#!/usr/bin/env python3
"""
s19_interrupts.py: Implementation of Real-Time Agent Interrupt Injection.

Motto: "Ctrl+C steers the agent; you're always in control"

This module addresses the "Runaway Agent" problem. In previous sessions, once 
an agent started a complex task, the user had to wait for it to finish or 
kill the process entirely. s19 introduces a "Soft Interrupt" pattern using 
asynchronous queues.

Key Architectural Concepts:
    1. Asyncio Queue: Acts as an out-of-band communication channel to inject 
       user commands into the agent's thought process mid-turn.
    2. Lifecycle Interruption: The agent checks the interrupt queue at two 
       critical points: 
          a) Before calling the LLM (to see if the goal changed).
          b) Before executing tools (to prevent unwanted actions).
    3. Graceful Degradation: If interrupted, the agent is instructed to 
       immediately stop, summarize its current progress, and wait for 
       further instructions.
    4. Signal Handling: The main loop distinguishes between a Ctrl+C at the 
       command prompt (system exit) and a Ctrl+C during agent activity 
       (task interrupt).

Operational Flow:
    - User hits Ctrl+C during agent loop -> Message put in Queue.
    - Agent finishes current tool -> Sees Queue message -> Appends to history.
    - Agent summarizes and stops.
"""

# === Standard Library Imports ===
import asyncio      # Asynchronous I/O and task management
import sys          # System-specific parameters
from typing import List, Dict, Any, Tuple, Optional  # For strict type hinting

# === Local Module Imports ===
# We leverage the core's async capabilities and standard toolsets.
from core import (
    client,            # The Anthropic API client
    MODEL,             # The specific model ID
    EXTENDED_TOOLS,    # Standard file and shell tools
    ASYNC_DISPATCH     # Dictionary mapping tool names to async handlers
)

# === Configuration and Global State ===

# Specialized System Prompt: Defines the behavior for handling interrupts.
SYSTEM: str = (
    f"You are a coding agent. "
    "If you receive a message starting with [INTERRUPT], you must stop your "
    "current sequence of actions immediately. Summarize the work you have "
    "completed so far, what remains to be done, and then wait for the user's "
    "next instruction."
)

# A thread-safe asynchronous queue for passing interrupt strings to the agent.
# Type Hint: asyncio.Queue[str]
interrupt_queue: asyncio.Queue = asyncio.Queue()


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
        # Catch internal tool errors to prevent crashing the entire turn
        output = f"Execution Error: {e}"
        
    # Print a snippet of the tool output for the user
    print(str(output)[:200])
    
    return block.id, str(output)


# === Interruptible Agent Loop ===

async def agent_loop_interruptible(messages: List[Dict[str, Any]]) -> None:
    """
    An autonomous agent loop that monitors an interrupt queue between steps.

    Args:
        messages (List[Dict[str, Any]]): The active conversation history.
    """
    while True:
        # 1. PRE-MODEL CHECK: Check if an interrupt arrived while we were idling
        if not interrupt_queue.empty():
            # Retrieve the interrupt message
            interrupt_msg = await interrupt_queue.get()
            print(f"\n\033[31m[INTERRUPT] {interrupt_msg}\033[0m")
            # Inject the interrupt into the conversation history
            messages.append({"role": "user", "content": interrupt_msg})

        # Define the streaming call (runs in a separate thread to keep loop responsive)
        def _blocking_stream_call():
            with client.messages.stream(
                model=MODEL, 
                system=SYSTEM, 
                messages=messages,
                tools=EXTENDED_TOOLS, 
                max_tokens=8000,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                return stream.get_final_message()

        # Execute the thinking turn
        response = await asyncio.get_event_loop().run_in_executor(None, _blocking_stream_call)
        print() # Newline after response text
        
        # Append assistant's response to history
        messages.append({"role": "assistant", "content": response.content})
        
        # Exit if the model has provided a final answer
        if response.stop_reason != "tool_use":
            return

        # 2. PRE-TOOL CHECK: Check if an interrupt arrived while the model was thinking
        if not interrupt_queue.empty():
            interrupt_msg = await interrupt_queue.get()
            print(f"\n\033[31m[INTERRUPT] Stopping before tool execution: {interrupt_msg}\033[0m")
            # Inject interrupt. The loop continues to next iteration where model sees this.
            messages.append({"role": "user", "content": interrupt_msg})
            continue 

        # 3. TOOL EXECUTION: Run all requested tools in parallel
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        
        # Dispatch tools and wait for all to finish
        execution_pairs = await asyncio.gather(*[dispatch_one_tool(b) for b in tool_blocks])
        results_map = dict(execution_pairs)
        
        # Format results for the Anthropic API
        turn_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": results_map[b.id]}
            for b in tool_blocks
        ]
        
        # Append tool results to history
        messages.append({"role": "user", "content": turn_results})


# === Main REPL with Signal Handling ===

async def main() -> None:
    """
    Initializes the terminal interaction and manages the interrupt signal logic.
    """
    # UI Header
    print("\033[90ms19: interrupts | Ctrl+C mid-task → injects stop | Ctrl+C at prompt → exit\033[0m\n")
    
    # Session History
    history: List[Dict[str, Any]] = []
    
    # Reference to the currently running agent task
    active_agent_task: Optional[asyncio.Task] = None
    
    loop = asyncio.get_event_loop()

    while True:
        try:
            # Capture user input in a non-blocking way
            query = await loop.run_in_executor(None, lambda: input("\033[36ms19 >> \033[0m").strip())
        except KeyboardInterrupt:
            # Case 1: Ctrl+C pressed at the command prompt -> Exit program
            print("\n  User requested exit. Goodbye.")
            if active_agent_task and not active_agent_task.done():
                active_agent_task.cancel()
            break
        except EOFError:
            # Case 2: Ctrl+D pressed -> Exit program
            break

        # Standard exit commands
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query
        history.append({"role": "user", "content": query})
        
        # Create a background task for the agent loop so we can continue monitoring for Ctrl+C
        active_agent_task = asyncio.create_task(agent_loop_interruptible(history))

        try:
            # Wait for the agent to finish its turns
            await active_agent_task
        except asyncio.CancelledError:
            # Handle manual task cancellation
            pass
        except KeyboardInterrupt:
            # Case 3: Ctrl+C pressed while the agent is active -> Inject Interrupt
            interrupt_instruction = (
                "[INTERRUPT] The user has requested an immediate pause by pressing Ctrl+C. "
                "Stop your current sequence, summarize your progress, and wait for instructions."
            )
            # Put the instruction into the queue for the loop to pick up
            await interrupt_queue.put(interrupt_instruction)
            
            print("\033[31m\n  Interrupt detected! Queuing stop command — agent will respond after current tool.\033[0m")
            
            try:
                # Give the agent 30 seconds to finish its current tool and summarize
                await asyncio.wait_for(active_agent_task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                print("  System: Agent failed to summarize within timeout. Forcing task cancellation.")
                if not active_agent_task.done():
                    active_agent_task.cancel()
        
        # Separator for the next interaction turn
        print()


if __name__ == "__main__":
    # Start the asynchronous event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass