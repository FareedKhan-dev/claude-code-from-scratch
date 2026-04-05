#!/usr/bin/env python3
"""
s06_context_compact.py: Implementation of Automated 3-Layer Context Management.

Motto: "Context will fill up; you need a way to make room"

This module addresses the 'Context Window' limitation of LLMs. As a conversation 
grows, the verbatim history consumes more tokens, eventually leading to 
API errors or performance degradation. This script implements a rolling 
compression mechanism similar to the 'Claude Code' CLI.

Compression Architecture:
    1. Layer 1 (Verbatim): The last N messages are kept exactly as they 
       happened to maintain the immediate "short-term" context.
    2. Layer 2 (Summarization): Older messages are sent to the model to be 
       condensed into a concise summary of decisions and actions.
    3. Layer 3 (Persistence): The generated summary is written to a 
       Markdown file (`.agent_memory.md`) on disk, allowing context to 
       survive across different sessions or restarts.

Trigger:
    The compression is triggered automatically when the estimated character 
    count of the conversation history exceeds a predefined threshold.
"""

# === Standard Library Imports ===
import os      # Operating system interfaces
import sys     # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Union, Optional  # For strict type hinting

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    stream_loop        # The core autonomous loop logic
)

# === Configuration and Constants ===

# Threshold to trigger compression (approx. 40k chars ≈ 10k tokens)
COMPRESS_THRESHOLD: int = 40_000

# Number of most recent messages to preserve verbatim (Layer 1)
KEEP_RECENT: int = 6

# The path where the 'Long-term Memory' is persisted (Layer 3)
MEMORY_FILE: Path = Path(".agent_memory.md")

# === Context Utility Functions ===

def _estimate_size(messages: List[Dict[str, Any]]) -> int:
    """
    Estimates the total character count of the conversation history.

    This acts as a proxy for token count to determine when to compress.

    Args:
        messages (List[Dict[str, Any]]): The list of conversation turns.

    Returns:
        int: Total estimated character count.
    """
    total = 0 # Initialize counter
    for msg in messages:
        content = msg.get("content", "")
        # Handle simple string content (usually user messages)
        if isinstance(content, str):
            total += len(content)
        # Handle structured list content (usually assistant or tool results)
        elif isinstance(content, list):
            for block in content:
                # Check for dictionary-style blocks
                if isinstance(block, dict):
                    total += len(str(block.get("text", "") or block.get("content", "")))
                # Check for Anthropic SDK block objects
                elif hasattr(block, "text"):
                    total += len(block.text or "")
    return total


def _summarize(messages: List[Dict[str, Any]]) -> str:
    """
    Utilizes the LLM to condense a segment of the conversation history.

    Args:
        messages (List[Dict[str, Any]]): The slice of history to summarize.

    Returns:
        str: A concise textual summary of the provided history.
    """
    # Construct a raw text representation of the messages for the summarizer
    text_to_summarize = "\n\n".join(
        f"[{m['role']}]: " + (
            m["content"] if isinstance(m["content"], str)
            else " ".join(
                (b.get("text", "") if isinstance(b, dict) else getattr(b, "text", ""))
                for b in (m["content"] if isinstance(m["content"], list) else [])
            )
        )
        for m in messages
    )
    
    # Prompt the model specifically for summarization
    # We use a 20k char slice of the input to ensure the summarization call itself doesn't fail
    response = client.messages.create(
        model=MODEL,
        system=(
            "You are a context compressor. Summarize the provided conversation history "
            "concisely. Retain all critical technical decisions, file paths mentioned, "
            "code changes made, and pending tasks. Ignore trivial back-and-forth."
        ),
        messages=[{"role": "user", "content": f"Summarize this history:\n\n{text_to_summarize[:20000]}"}],
        max_tokens=2000,
    )
    
    # Return the aggregated text from the summary response
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def maybe_compress(messages: List[Dict[str, Any]]) -> bool:
    """
    Evaluates context size and performs compression if the threshold is exceeded.

    This function modifies the 'messages' list in-place by replacing older turns 
    with a single summary turn.

    Args:
        messages (List[Dict[Dict[str, Any]]]): The active message history.

    Returns:
        bool: True if compression occurred, False otherwise.
    """
    # Layer 0: Only compress if we are over the size limit
    if _estimate_size(messages) < COMPRESS_THRESHOLD:
        return False
    
    # Layer 0: Only compress if we have more messages than the 'recent' buffer
    if len(messages) <= KEEP_RECENT:
        return False

    # Visual feedback: Log compression start in Gray (\033[90m)
    print("\033[90m  [compress] Context large — condensing older history...\033[0m")
    
    # Split history into 'old' (to be compressed) and 'recent' (to be kept)
    old_messages = messages[:-KEEP_RECENT]
    recent_messages = messages[-KEEP_RECENT:]

    # Layer 2: Generate the textual summary
    summary = _summarize(old_messages)

    # Layer 3: Persist summary to a Markdown file on disk
    try:
        MEMORY_FILE.write_text(
            f"# Agent Context Memory\n*Last updated: {os.getcwd()}*\n\n{summary}\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"\033[31m  [error] Failed to persist memory: {e}\033[0m")

    # Rebuild the message history list
    messages.clear()
    
    # 1. Inject the summary as the new "starting context"
    messages.append({
        "role": "user",
        "content": f"[Context summary of previous turns]:\n\n{summary}",
    })
    # 2. Add an assistant acknowledgement to maintain the user/assistant turn alternating rule
    messages.append({
        "role": "assistant",
        "content": "Understood. I have integrated the summary of our previous progress into my current context.",
    })
    # 3. Restore the most recent verbatim messages
    messages.extend(recent_messages)

    # Visual feedback: Log completion
    print(f"\033[90m  [compress] Done. Collapsed {len(old_messages)} messages into 1 summary. Saved to {MEMORY_FILE}\033[0m")
    return True


def agent_loop_with_compression(messages: List[Dict[str, Any]]) -> None:
    """
    A wrapper for stream_loop that ensures compression logic is checked 
    after every complete agent turn.

    Args:
        messages (List[Dict[str, Any]]): The active message history.
    """
    # 1. Execute the standard autonomous agent loop
    stream_loop(messages, EXTENDED_TOOLS, EXTENDED_DISPATCH)
    
    # 2. Evaluate if the history now needs compression
    maybe_compress(messages)


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s06 'Context Compacting' agent.
    """
    # Define basic identity system prompt
    system_prompt: str = f"You are a coding agent at {os.getcwd()}. Use tools to solve tasks."

    # Initialize history
    history: List[Dict[str, Any]] = []

    # Check for existing memory file from previous runs (Session Persistence)
    if MEMORY_FILE.exists():
        try:
            mem_content = MEMORY_FILE.read_text(encoding="utf-8")
            print(f"\033[90m  [memory] Restoring context from {MEMORY_FILE}...\033[0m")
            # Seed the history with the saved memory
            history = [
                {"role": "user",      "content": f"[Previous Session Memory]:\n\n{mem_content}"},
                {"role": "assistant", "content": "Memory loaded. I am ready to continue where we left off."},
            ]
        except Exception as e:
            print(f"\033[31m  [error] Could not read memory file: {e}\033[0m")

    # UI Header
    print(f"\033[90ms06: context compression at ~{COMPRESS_THRESHOLD//1000}k chars | memory → {MEMORY_FILE}\033[0m\n")

    # Main REPL loop
    while True:
        try:
            # User Input in Cyan
            query: str = input("\033[36ms06 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting session.")
            sys.exit(0)

        # Standard exit handlers
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record new user query
        history.append({"role": "user", "content": query})
        
        # Run agent loop with post-turn compression check
        agent_loop_with_compression(history)
        
        # Visual spacing
        print()


if __name__ == "__main__":
    # Script entry point
    main()