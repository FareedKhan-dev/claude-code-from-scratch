"""
agents/core.py: Core components for the AI agent sessions.

This module provides the shared foundation for all agent sessions (s01–s23).
It centralizes all core functionalities to ensure that no logic is duplicated
across session-specific files. Each session file imports from this module and
only contains the new mechanism being introduced in that session.

Exports:
    - client (Anthropic): The configured Anthropic API client.
    - MODEL (str): The ID of the language model to be used.
    - DEFAULT_SYSTEM (str): The default system prompt for the agent.
    - SNAPSHOTS (dict): An in-memory store for file content snapshots.
    - Synchronous Tools: run_bash, run_read, run_write, run_grep, run_glob, run_revert.
    - Asynchronous Tools: async_bash, async_read, async_write, async_grep, async_glob.
    - Tool Schemas: BASIC_TOOLS, EXTENDED_TOOLS for the Anthropic API.
    - Dispatch Maps: BASIC_DISPATCH, EXTENDED_DISPATCH, ASYNC_DISPATCH.
    - Governance: load_rules(), check_permission().
    - Agent Loops: stream_loop(), dispatch_tools().
"""

# Import standard library modules
import os  # Operating system interfaces
import re  # Regular expression operations
import asyncio  # Asynchronous I/O framework
import subprocess  # Subprocess management for shell commands
import glob as _glob  # Unix style pathname pattern expansion
from pathlib import Path  # Object-oriented filesystem paths
from typing import Dict, List, Tuple, Optional, Any  # Type hinting support

# === Optional Dependencies for Enhanced User Experience ===

# Attempt to configure 'readline' for better CLI input handling on Unix-based systems
try:
    import readline  # Provides line editing and history features
    # Disable special character binding that can interfere with terminal output
    readline.parse_and_bind("set bind-tty-special-chars off")
    # Enable handling of 8-bit input characters
    readline.parse_and_bind("set input-meta on")
    # Enable output of 8-bit characters
    readline.parse_and_bind("set output-meta on")
    # Prevent conversion of 8-bit characters to ASCII sequences
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    # Fail silently if readline is unavailable (e.g., on standard Windows installations)
    pass

# Attempt to initialize 'colorama' for cross-platform colored terminal support
try:
    from colorama import init as _colorama_init  # Import the initialization function
    _colorama_init()  # Execute initialization to wrap stdout/stderr
except ImportError:
    # Fail silently if colorama is not installed in the environment
    pass

# Import third-party libraries
import yaml  # YAML parser and emitter for configuration files
from anthropic import Anthropic  # Official Anthropic API Python SDK
from dotenv import load_dotenv  # Loads variables from .env into environment

# === Configuration and Initialization ===

# Load environment variables from a .env file, allowing local overrides of system vars
load_dotenv(override=True)

# Check if a custom base URL is set (useful for local proxies or specific API gateways)
if os.getenv("ANTHROPIC_BASE_URL"):
    # Remove the standard auth token to prevent conflicts with custom gateways
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# Initialize the global Anthropic client using the base URL from environment
client: Anthropic = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# Define the Model ID used for all completions, sourced from environment variables
MODEL: str = os.environ.get("MODEL_ID", "claude-3-5-sonnet-20240620")

# Define the default system instructions for the AI agent
DEFAULT_SYSTEM: str = f"You are a coding agent at {os.getcwd()}. Use tools to solve tasks. Act, don't explain."

# === In-Memory File Snapshot Store ===

# Global registry to store file contents before modification to allow 'undo' operations
# Key: File path (str) | Value: Content (str) or None if file was newly created
SNAPSHOTS: Dict[str, Optional[str]] = {}

# === Security: Dangerous Command Patterns ===

# A blacklist of shell command fragments that should never be executed for safety
_ALWAYS_BLOCK: List[str] = [
    "rm -rf /",      # Prevent root filesystem deletion
    "sudo",          # Prevent privilege escalation
    "shutdown",      # Prevent system termination
    "reboot",        # Prevent system restart
    "> /dev/",       # Prevent direct hardware or system device writing
    ":(){ :|:& };:"  # Prevent fork bombs
]

# === Synchronous Tool Implementations ===

def run_bash(command: str) -> str:
    """
    Executes a shell command synchronously and returns the output.

    Args:
        command (str): The raw shell command string to execute.

    Returns:
        str: Combined stdout and stderr output, truncated if necessary.
    """
    # Security check: verify the command doesn't contain blacklisted patterns
    if any(blocked in command for blocked in _ALWAYS_BLOCK):
        return "Error: dangerous command blocked"
    
    try:
        # Execute command in the current working directory using the system shell
        result = subprocess.run(
            command, shell=True, cwd=os.getcwd(),
            capture_output=True, text=True, timeout=120
        )
        # Combine standard output and error output, then strip whitespace
        output = (result.stdout + result.stderr).strip()
        # Return output or a placeholder, capped at 50k chars to protect context window
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        # Handle cases where the command runs longer than the 120s limit
        return "Error: timeout (120s)"
    except Exception as e:
        # Return any other execution errors as a string
        return f"Error: {e}"


def run_read(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    Reads a file from disk with optional line-range slicing and line numbering.

    Args:
        path (str): Path to the file.
        start_line (int, optional): Starting line (1-indexed).
        end_line (int, optional): Ending line (1-indexed).

    Returns:
        str: Formatted file content with line numbers or error message.
    """
    try:
        # Open file with utf-8 encoding, replacing invalid characters to prevent crashes
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        
        # Convert 1-based human/AI indexing to 0-based Python indexing
        start_index = (start_line or 1) - 1
        # Set end index to requested line or default to end of file
        end_index = end_line or len(lines)
        
        # Build a string where every line is prefixed by its line number
        numbered_lines = "".join(
            f"{start_index + 1 + i:4d}\t{line}" 
            for i, line in enumerate(lines[start_index:end_index])
        )
        # Return formatted text, capped at 50k chars
        return numbered_lines[:50000] or "(empty file)"
    except FileNotFoundError:
        # Explicit error for missing files
        return f"Error: file not found: {path}"
    except Exception as e:
        # Generic error handling for permission issues, etc.
        return f"Error reading {path}: {e}"


def run_write(path: str, content: str) -> str:
    """
    Writes content to a file and stores a snapshot for potential restoration.

    Args:
        path (str): Destination file path.
        content (str): Text content to write.

    Returns:
        str: Status message indicating success or failure.
    """
    try:
        # Check if file exists to determine if we update or create
        if os.path.exists(path):
            # Read and store current content for 'revert' functionality
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                SNAPSHOTS[path] = f.read()
            action = "updated"
        else:
            # Mark as None in snapshots so 'revert' knows to delete the file
            SNAPSHOTS[path] = None
            action = "created"
        
        # Ensure the directory structure exists before writing the file
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        
        # Perform the actual file write
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"{action}: {path} (snapshot saved — use revert to undo)"
    except Exception as e:
        # Capture and return write-related exceptions
        return f"Error writing {path}: {e}"


def run_grep(pattern: str, path: str = ".", recursive: bool = True) -> str:
    """
    Performs a regex search within files using system grep or Windows findstr.

    Args:
        pattern (str): The regex pattern to look for.
        path (str): Search root directory.
        recursive (bool): Whether to search subdirectories.

    Returns:
        str: Matching lines with line numbers.
    """
    try:
        # Determine recursion flag for Unix grep
        flags = ["-r"] if recursive else []
        # Execute grep with line numbers (-n)
        result = subprocess.run(
            ["grep", "-n", *flags, pattern, path],
            capture_output=True, text=True, timeout=30
        )
        # Return results truncated to 10k chars to keep context lean
        return ((result.stdout + result.stderr).strip() or "(no matches)")[:10000]
    except FileNotFoundError:
        # Fallback mechanism for Windows environments without grep installed
        try:
            # Construct findstr command for common code file extensions
            command = f'findstr /S /N "{pattern}" "{path}\\*.py" "{path}\\*.js" "{path}\\*.md"'
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            return ((result.stdout + result.stderr).strip() or "(no matches)")[:10000]
        except Exception as e:
            return f"Error: grep/findstr failed: {e}"
    except subprocess.TimeoutExpired:
        return "Error: grep timeout"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    """
    Locates files matching a specific glob pattern.

    Args:
        pattern (str): Glob pattern (e.g., '**/*.py').

    Returns:
        str: Newline-separated list of found file paths.
    """
    # Perform recursive glob search using the standard library
    matches = _glob.glob(pattern, recursive=True)
    if not matches:
        return "(no matches)"
    # Sort for consistency and limit count to prevent massive context inflation
    return "\n".join(sorted(matches)[:200])


def run_revert(path: str) -> str:
    """
    Undoes the last write operation performed on a specific file path.

    Args:
        path (str): The file path to restore.

    Returns:
        str: Status message of the restoration.
    """
    # Check if a snapshot exists for this path
    if path not in SNAPSHOTS:
        return f"Error: no snapshot for {path}"
    
    # Retrieve and remove the snapshot from memory
    original_content = SNAPSHOTS.pop(path)
    
    if original_content is None:
        # If original_content was None, the file didn't exist before 'write'
        try:
            os.remove(path) # Revert by deleting the new file
            return f"reverted: deleted {path} (it was a new file)"
        except Exception as e:
            return f"Error deleting {path}: {e}"
    else:
        # If original_content existed, write it back to the file
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original_content)
            return f"reverted: {path}"
        except Exception as e:
            return f"Error reverting {path}: {e}"


# === Asynchronous Tool Wrappers ===

async def async_bash(command: str) -> str:
    """
    Asynchronous version of run_bash for non-blocking execution.

    Args:
        command (str): Shell command.

    Returns:
        str: Command output.
    """
    # Safety check (replicated for async context)
    if any(blocked in command for blocked in _ALWAYS_BLOCK):
        return "Error: dangerous command blocked"
    try:
        # Create an async subprocess
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        # Wait for the process to complete or timeout
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        # Decode bytes output and strip
        output = (stdout.decode() + stderr.decode()).strip()
        return output[:50000] if output else "(no output)"
    except asyncio.TimeoutError:
        return "Error: timeout (120s)"
    except Exception as e:
        return f"Error: {e}"


async def async_read(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Runs run_read in a separate thread to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_read, path, start_line, end_line)


async def async_write(path: str, content: str) -> str:
    """Runs run_write in a separate thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_write, path, content)


async def async_grep(pattern: str, path: str = ".", recursive: bool = True) -> str:
    """Runs run_grep in a separate thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_grep, pattern, path, recursive)


async def async_glob(pattern: str) -> str:
    """Runs run_glob in a separate thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_glob, pattern)


# === Tool Definitions for Anthropic API ===

# Basic tool list containing only the bash command
BASIC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]

# Extended tool list for full filesystem management
EXTENDED_TOOLS: List[Dict[str, Any]] = BASIC_TOOLS + [
    {
        "name": "read",
        "description": "Read a file. Optional start_line/end_line for a range (1-indexed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line":   {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Write content to a file. Snapshots previous content automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "grep",
        "description": "Search for a regex pattern in files under a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern":   {"type": "string"},
                "path":      {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": True},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern, e.g. '**/*.py'.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "revert",
        "description": "Restore a file to its state before the last write.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]

# === Dispatch Maps for Tool Routing ===

# Mapping names to synchronous tool functions
BASIC_DISPATCH: Dict[str, Any] = {
    "bash": lambda inp: run_bash(inp["command"]),
}

# Mapping names to full suite of synchronous tools
EXTENDED_DISPATCH: Dict[str, Any] = {
    "bash":   lambda inp: run_bash(inp["command"]),
    "read":   lambda inp: run_read(inp["path"], inp.get("start_line"), inp.get("end_line")),
    "write":  lambda inp: run_write(inp["path"], inp["content"]),
    "grep":   lambda inp: run_grep(inp["pattern"], inp.get("path", "."), inp.get("recursive", True)),
    "glob":   lambda inp: run_glob(inp["pattern"]),
    "revert": lambda inp: run_revert(inp["path"]),
}

# Mapping names to full suite of asynchronous tool wrappers
ASYNC_DISPATCH: Dict[str, Any] = {
    "bash":  lambda inp: async_bash(inp["command"]),
    "read":  lambda inp: async_read(inp["path"], inp.get("start_line"), inp.get("end_line")),
    "write": lambda inp: async_write(inp["path"], inp["content"]),
    "grep":  lambda inp: async_grep(inp["pattern"], inp.get("path", "."), inp.get("recursive", True)),
    "glob":  lambda inp: async_glob(inp["pattern"]),
}

# === Permission Governance ===

# Path to the permission configuration file
_PERM_CONFIG: Path = Path(__file__).parent.parent / "config" / "permissions.yaml"

def load_rules() -> Dict[str, List[Dict[str, str]]]:
    """
    Loads permission logic rules from a YAML configuration file.

    Returns:
        dict: The parsed rules dictionary.
    """
    try:
        # Attempt to read the permission YAML
        with open(_PERM_CONFIG, "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        # Return a restrictive default if config is missing
        return {"always_deny": [], "always_allow": [], "ask_user": []}


def check_permission(tool_name: str, input_str: str, rules: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Evaluates if a tool call is allowed based on hierarchical security rules.

    Args:
        tool_name (str): Name of the tool being called.
        input_str (str): String representation of the parameters.
        rules (dict, optional): Custom rules dict. Loads from file if None.

    Returns:
        tuple: (Is permitted?, Reason string)
    """
    # Load rules if not provided in arguments
    if rules is None:
        rules = load_rules()

    # Priority 1: Check if the input matches any 'always_deny' pattern
    for rule in rules.get("always_deny", []):
        if re.search(rule["pattern"], input_str, re.IGNORECASE):
            reason = rule.get("reason", "blocked by policy")
            # Print feedback in Red
            print(f"\033[31m[DENIED] {reason}\033[0m")
            return False, f"Denied: {reason}"

    # Priority 2: Check if the input matches any 'always_allow' pattern
    for rule in rules.get("always_allow", []):
        if re.search(rule["pattern"], input_str, re.IGNORECASE):
            return True, "allowed by policy"

    # Priority 3: Check if input requires explicit user confirmation
    for rule in rules.get("ask_user", []):
        if re.search(rule["pattern"], input_str, re.IGNORECASE):
            reason = rule.get("reason", "requires user confirmation")
            # Print feedback in Yellow
            print(f"\n\033[33m[PERMISSION] {tool_name}: {input_str[:100]}")
            print(f"  Reason: {reason}\033[0m")
            try:
                # Prompt user via terminal
                ans = input("  Allow? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n" # Default to 'no' on interrupt
            return (ans in ("y", "yes")), "user decision"

    # Priority 4: If no rules match, default to allowed
    return True, "allowed by default (no rule matched)"


# === Shared Agent Logic ===

def dispatch_tools(response_content: list, dispatch: dict) -> List[Dict[str, Any]]:
    """
    Processes tool use blocks from an assistant response and executes handlers.

    Args:
        response_content (list): The content blocks from the model's response.
        dispatch (dict): The mapping of tool names to functions.

    Returns:
        list: A list of tool_result objects to be sent back to the API.
    """
    results = [] # To hold tool_result items
    for block in response_content:
        # Ignore text blocks, only process tool_use blocks
        if block.type != "tool_use":
            continue

        tool_name = block.name # Name of the tool requested
        tool_input = block.input # Params provided by the model
        tool_use_id = block.id # ID required for returning results
        handler = dispatch.get(tool_name) # Fetch handler from map
        
        # UI: Print the tool call in Yellow
        first_val = str(list(tool_input.values())[0])[:80] if tool_input else ""
        print(f"\033[33m[{tool_name}] {first_val}...\033[0m")

        if handler:
            try:
                # Call the mapped function with input dict
                output = handler(tool_input)
            except Exception as e:
                # Catch internal handler errors
                output = f"Error during tool execution: {e}"
        else:
            # Handle cases where the model hallucinates a tool name
            output = f"Error: Unknown tool '{tool_name}'"
        
        # Log a snippet of the tool's output to the terminal
        print(str(output)[:300])
        
        # Structure the result as required by the Anthropic API
        results.append({
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(output),
        })
    return results


def stream_loop(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    dispatch: Dict,
    system: Optional[str] = None,
    extra_kwargs: Optional[Dict[str, Any]] = None
):
    """
    The main conversation loop that streams model output and handles tool orchestration.

    Args:
        messages (list): The rolling conversation history.
        tools (list): Schemas of tools available to the model.
        dispatch (dict): Function map for executing tools.
        system (str, optional): System instructions.
        extra_kwargs (dict, optional): Extra parameters for the API call.
    """
    # Use default system prompt if none provided
    system = system or DEFAULT_SYSTEM
    # Initialize extra params as empty dict if None
    extra_kwargs = extra_kwargs or {}
    
    while True:
        # Indicate the thinking phase in Cyan
        print("\n\033[36m> Thinking...\033[0m")
        
        # Open a streaming connection to the Anthropic API
        with client.messages.stream(
            model=MODEL,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=8000,
            **extra_kwargs,
        ) as stream:
            # Print text chunks as they arrive for a responsive UI
            for text in stream.text_stream:
                print(text, end="", flush=True)
            # Finalize the message once streaming is complete
            response = stream.get_final_message()
        
        # Print a newline for visual separation
        print()
        # Record the assistant's message in the history
        messages.append({"role": "assistant", "content": response.content})
        
        # Break the loop if the model stopped for any reason other than calling tools
        if response.stop_reason != "tool_use":
            return response
            
        # Execute tool calls and gather results
        results = dispatch_tools(response.content, dispatch)
        # Append tool results to history for the model to see in the next iteration
        messages.append({"role": "user", "content": results})