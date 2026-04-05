#!/usr/bin/env python3
"""
s17_session_management.py: Implementation of Persistent Session Lifecycle and Forking.

Motto: "Every conversation is saved; pick up where you left off"

This module addresses the "Volatility" problem in AI interactions. By default, 
agent context exists only in memory and is lost if the script terminates. 
s17 implements a full serialization layer that persists history to JSON, 
enabling advanced features like session resumption and parallel exploration (forking).

Key Architectural Concepts:
    1. Serialization: Converting complex Anthropic SDK objects (like ToolUse blocks) 
       into plain JSON-serializable dictionaries.
    2. Session Identity: Every session is assigned a unique 8-character ID and 
       an optional human-readable title.
    3. Auto-save: The system persists the state to disk after every successful 
       interaction turn to prevent data loss.
    4. Forking: Creating a deep copy of an existing session under a new ID, 
       allowing the user to test different prompts or strategies from a 
       shared starting point.

Special REPL Commands:
    :sessions          - List all saved sessions from the .sessions/ directory.
    :resume <id>       - Load a specific session and continue the chat.
    :fork <id>         - Create a new session starting from an old session's history.
    :title <text>      - Set a human-readable name for the current session.
    :save              - Manually trigger a save operation.
"""

# === Standard Library Imports ===
import os          # Operating system interfaces
import json        # JSON encoding and decoding
import uuid        # Unique identifier generation
import sys         # System-specific parameters
from datetime import datetime  # Date and time manipulation
from pathlib import Path       # Object-oriented filesystem paths
from typing import List, Dict, Any, Union, Optional  # For strict type hinting

# === Local Module Imports ===
from core import (
    EXTENDED_TOOLS,    # Standard file/shell tools
    EXTENDED_DISPATCH, # Mapping for standard tools
    stream_loop        # The core autonomous loop logic
)

# === Configuration and Global State ===

# Directory where session JSON files are persisted
SESSIONS_DIR: Path = Path(".sessions")
SESSIONS_DIR.mkdir(exist_ok=True) # Ensure the directory exists at startup

# Global persona for the agent
SYSTEM: str = f"You are a coding agent at {os.getcwd()}."

# === Session Management Logic ===

def create_new_session() -> Dict[str, Any]:
    """
    Initializes a fresh session data structure.

    Returns:
        Dict[str, Any]: A dictionary containing session metadata and empty history.
    """
    return {
        "id": uuid.uuid4().hex[:8],         # Unique 8-character hex ID
        "created": datetime.now().isoformat(), # ISO 8601 creation timestamp
        "updated": datetime.now().isoformat(), # Last modification timestamp
        "title": "New Session",             # Default human-readable title
        "messages": []                      # Empty conversation history
    }


def _serialize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converts Anthropic SDK message objects into plain Python dictionaries.
    
    This is necessary because raw SDK 'Block' objects are not directly 
    serializable to JSON.

    Args:
        messages (List[Dict[str, Any]]): The list of message history.

    Returns:
        List[Dict[str, Any]]: A JSON-compatible version of the history.
    """
    serialized = []
    for msg in messages:
        content = msg.get("content")
        # If content is a list (contains Text or ToolUse blocks)
        if isinstance(content, list):
            clean_content = []
            for block in content:
                # If it's a Pydantic model from the SDK, use model_dump()
                if hasattr(block, "model_dump"):
                    clean_content.append(block.model_dump())
                # If it's a generic object with a __dict__, use that
                elif hasattr(block, "__dict__"):
                    clean_content.append(block.__dict__)
                # Otherwise, assume it's already a dictionary
                else:
                    clean_content.append(block)
            content = clean_content
            
        serialized.append({
            "role": msg["role"], 
            "content": content
        })
    return serialized


def save_session(session_data: Dict[str, Any]) -> None:
    """
    Persists the current session state to a JSON file on disk.

    Args:
        session_data (Dict[str, Any]): The session object to save.
    """
    # Update the 'updated' timestamp before saving
    session_data["updated"] = datetime.now().isoformat()
    
    # Define the file path: .sessions/<id>.json
    file_path = SESSIONS_DIR / f"{session_data['id']}.json"
    
    # Prepare serializable content
    json_ready_data = {
        **session_data,
        "messages": _serialize_messages(session_data["messages"])
    }
    
    # Write to disk with indentation for readability
    file_path.write_text(json.dumps(json_ready_data, indent=2), encoding="utf-8")


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves a session from disk by its ID.

    Args:
        session_id (str): The 8-character ID of the session.

    Returns:
        Optional[Dict[str, Any]]: The loaded session data, or None if not found.
    """
    file_path = SESSIONS_DIR / f"{session_id}.json"
    if not file_path.exists():
        return None
    
    try:
        # Read and parse the JSON file
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        print(f"\033[31m  [error] Failed to load session {session_id}: {e}\033[0m")
        return None


def list_all_sessions() -> List[Dict[str, Any]]:
    """
    Scans the sessions directory and returns a list of all saved session objects.

    Sorted by last modified time (most recent first).

    Returns:
        List[Dict[str, Any]]: A list of session data dictionaries.
    """
    sessions = []
    # Find all JSON files and sort by modification time on disk
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    for f in files:
        try:
            sessions.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue # Skip corrupted files
    return sessions


def print_sessions_table() -> None:
    """
    Prints a formatted table of existing sessions to the console.
    """
    sessions = list_all_sessions()
    if not sessions:
        print("  (No saved sessions found in .sessions/)")
        return
        
    print("\n  \033[4mID        LAST UPDATED         TITLE (MESSAGES)\033[0m")
    for s in sessions:
        msg_count = len(s.get("messages", []))
        # Format: ID (Cyan) | Timestamp | Title | Msg Count (Gray)
        print(f"  \033[36m{s['id']}\033[0m  {s['updated'][:19]}  {s['title'][:40]:40} \033[90m({msg_count} msgs)\033[0m")
    print()


# === Main Execution Block (REPL) ===

def main() -> None:
    """
    Initializes the terminal-based REPL with session management commands.
    """
    # UI Header
    print("\033[90ms17: session management | commands: :sessions :resume :fork :title :save\033[0m\n")
    
    # Initialize the current session context
    current_session = create_new_session()
    print(f"  New session created: \033[36m{current_session['id']}\033[0m\n")

    # Main Command-Line Loop
    while True:
        try:
            # Multi-command prompt including the active session ID
            query: str = input(f"\033[36ms17 ({current_session['id']}) >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Auto-save on interrupt/exit
            save_session(current_session)
            print(f"\n  Session {current_session['id']} auto-saved. Exiting.")
            break

        # Standard session termination check
        if query.lower() in ("q", "exit", "quit"):
            save_session(current_session)
            print(f"  Session {current_session['id']} auto-saved. Goodbye.")
            break

        # --- Command Processing ---

        # Command: List all sessions
        if query == ":sessions":
            print_sessions_table()
            continue
            
        # Command: Resume an existing session
        if query.startswith(":resume "):
            target_id = query[8:].strip()
            loaded = load_session(target_id)
            if loaded:
                current_session = loaded
                print(f"  Resumed: \033[36m{current_session['id']}\033[0m — '{current_session['title']}' ({len(current_session['messages'])} msgs)")
            else:
                print(f"  Error: Session '{target_id}' not found.")
            continue
            
        # Command: Fork (Clone) a session
        if query.startswith(":fork "):
            source_id = query[6:].strip()
            source = load_session(source_id)
            if source:
                # Create a new session starting with the old one's messages
                new_sid = uuid.uuid4().hex[:8]
                current_session = {
                    **source,
                    "id": new_sid,
                    "title": f"Fork of {source['title'][:30]}",
                    "created": datetime.now().isoformat(),
                    "updated": datetime.now().isoformat()
                }
                save_session(current_session)
                print(f"  Forked {source_id} → \033[36m{new_sid}\033[0m")
            else:
                print(f"  Error: Session '{source_id}' not found.")
            continue
            
        # Command: Rename the current session
        if query.startswith(":title "):
            new_title = query[7:].strip()
            current_session["title"] = new_title
            save_session(current_session)
            print(f"  Title updated to: '{new_title}'")
            continue
            
        # Command: Manual save
        if query == ":save":
            save_session(current_session)
            print(f"  Manual save completed: {current_session['id']}")
            continue

        # --- Standard Agent Interaction ---

        # If it's a new session, auto-title it based on the first query
        if not current_session["messages"]:
            current_session["title"] = query[:50]
        
        # Append user query to history
        current_session["messages"].append({"role": "user", "content": query})
        
        # Execute the thinking/acting loop
        stream_loop(
            messages=current_session["messages"], 
            tools=EXTENDED_TOOLS, 
            dispatch=EXTENDED_DISPATCH, 
            system=SYSTEM
        )
        
        # Auto-save after every assistant turn
        save_session(current_session)
        print()


if __name__ == "__main__":
    # Script entry point
    main()