#!/usr/bin/env python3
"""
s21_mcp_runtime.py: Official Model Context Protocol (MCP) Runtime.

Motto: "Any server, any tool; the world connects here"

This module implements the MCP Client, allowing the agent to break out of its 
local environment and interact with a vast ecosystem of third-party services. 
Instead of hardcoding every integration, the agent reads a configuration file, 
connects to external "MCP Servers," and dynamically populates its toolset 
with the capabilities those servers provide.

Key Architectural Concepts:
    1. Dynamic Tool Discovery: At startup, the agent queries each configured 
       server for its available tools and their JSON schemas.
    2. Tool Name Prefixing: To prevent name collisions between different 
       servers, tools are namespaced as: `mcp__<server_name>__<tool_name>`.
    3. Stdio Transport: Communication with MCP servers happens over 
       Standard Input/Output (stdio), allowing the agent to spawn server 
       processes and interact via JSON-RPC.
    4. Lifecycle Management: Servers are initialized at startup and 
       gracefully shut down when the session terminates.

Prerequisites:
    - pip install mcp
    - config/mcp_config.yaml must be populated with server definitions.
"""

# === Standard Library Imports ===
import asyncio      # Asynchronous I/O for concurrent server communication
import os           # Operating system interfaces
import yaml         # YAML parsing for server configurations
import sys          # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Tuple, Optional, Set, Callable # Type hinting

# === Local Module Imports ===
from core import (
    client,            # The Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Local built-in tools (bash, read, write, etc.)
    ASYNC_DISPATCH     # Mapping for local async tool handlers
)

# === Configuration and Global Registries ===

# Path to the MCP configuration file containing server commands and arguments
_CONFIG_PATH: Path = Path(__file__).parent.parent / "config" / "mcp_config.yaml"

# Global registries to manage the state of external tool connections
# Maps server name (str) -> Active MCP ClientSession object
MCP_SESSIONS: Dict[str, Any] = {}

# Maps prefixed name "mcp__srv__tool" -> Tuple(server_name, original_tool_name)
# This acts as a routing table for incoming tool calls
MCP_TOOL_MAP: Dict[str, Tuple[str, str]] = {}

# === MCP SDK Dependency Handling ===

try:
    # Attempt to import official MCP Client components
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP: bool = True
except ImportError:
    # Graceful fallback if the user hasn't installed the 'mcp' package
    HAS_MCP = False
    print("\033[33mWarning: 'mcp' package not found. Run: pip install mcp\033[0m")
    print("\033[33mContinuing with built-in tools only.\033[0m\n")


# === MCP Connection Logic ===

async def connect_mcp_servers() -> List[Dict[str, Any]]:
    """
    Initializes connections to all MCP servers defined in the config file.

    It performs the following for each server:
    1. Spawns the server process via stdio.
    2. Initializes an MCP Session.
    3. Fetches the list of available tools.
    4. Builds tool schemas compatible with the Anthropic API.

    Returns:
        List[Dict[str, Any]]: A list of tool definitions to be sent to the LLM.
    """
    if not HAS_MCP:
        return []

    # Check if the configuration file exists
    if not _CONFIG_PATH.exists():
        print(f"\033[33m  [MCP] Configuration not found at {_CONFIG_PATH}\033[0m")
        return []

    try:
        # Parse the YAML configuration
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"\033[31m  [MCP] Failed to parse config: {e}\033[0m")
        return []

    discovered_tools = []
    
    # Iterate through the defined servers in the config
    for srv_cfg in (config.get("servers") or []):
        server_name = srv_cfg.get("name", "unnamed_server")
        
        try:
            # Currently only 'stdio' transport is supported in this runtime
            if srv_cfg.get("transport", "stdio") == "stdio":
                # Prepare the execution parameters (command and CLI arguments)
                params = StdioServerParameters(
                    command=srv_cfg["command"], 
                    args=srv_cfg.get("args", [])
                )
                
                # Use a context manager to establish the stdio connection
                # Note: In a production long-running app, we manage __aenter__ manually
                read_stream, write_stream = await stdio_client(params).__aenter__()
                
                # Initialize the MCP Client Session
                session = await ClientSession(read_stream, write_stream).__aenter__()
                await session.initialize()
                
                # Query the server for its available tools
                mcp_response = await session.list_tools()
                tool_list = mcp_response.tools
                
                # Store the session for later tool execution
                MCP_SESSIONS[server_name] = session
                print(f"\033[90m  [MCP] {server_name}: Connected ({len(tool_list)} tools)\033[0m")
                
                # Register each tool with a unique prefix
                for tool in tool_list:
                    prefixed_name = f"mcp__{server_name}__{tool.name}"
                    # Record the mapping for the dispatcher
                    MCP_TOOL_MAP[prefixed_name] = (server_name, tool.name)
                    
                    # Transform MCP schema into Anthropic Tool Schema
                    discovered_tools.append({
                        "name": prefixed_name,
                        "description": f"[{server_name}] {tool.description or tool.name}",
                        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
                    })
            else:
                print(f"\033[33m  [MCP] {server_name}: transport '{srv_cfg['transport']}' not supported\033[0m")
        except Exception as e:
            # Log failure for the specific server but allow others to continue
            print(f"\033[31m  [MCP] Failed to connect to '{server_name}': {e}\033[0m")
            
    return discovered_tools


async def execute_mcp_tool(prefixed_name: str, arguments: Dict[str, Any]) -> str:
    """
    Routes a tool call to the appropriate MCP server and executes it.

    Args:
        prefixed_name (str): The name of the tool (mcp__server__tool).
        arguments (Dict[str, Any]): The arguments provided by the LLM.

    Returns:
        str: The result of the tool execution as a string.
    """
    # Look up which server and original tool name this corresponds to
    if prefixed_name not in MCP_TOOL_MAP:
        return f"Error: MCP tool '{prefixed_name}' is not in the registry."
    
    srv_name, original_tool_name = MCP_TOOL_MAP[prefixed_name]
    session = MCP_SESSIONS.get(srv_name)
    
    if not session:
        return f"Error: MCP session for '{srv_name}' is inactive."
    
    try:
        # Perform the actual remote call via the MCP SDK
        result = await session.call_tool(original_tool_name, arguments)
        
        # Aggregate the text content from the MCP response
        output_parts = [
            item.text for item in (result.content or []) 
            if hasattr(item, "text")
        ]
        # Return joined output, capped at 50k chars for context safety
        return "\n".join(output_parts)[:50000] or "(no output received)"
    except Exception as e:
        # Capture and return remote execution errors
        return f"Error during MCP execution: {e}"


# === Unified Dispatch Logic ===

async def dispatch_one_tool(block: Any, mcp_names: Set[str]) -> Tuple[str, str]:
    """
    Routes a single tool call to either the local dispatch or the MCP dispatch.

    Args:
        block (Any): The tool_use block from the LLM.
        mcp_names (Set[str]): The set of registered MCP tool names.

    Returns:
        Tuple[str, str]: (tool_use_id, output)
    """
    tool_input = block.input
    tool_name = block.name
    
    # UI Feedback: Log tool usage in Yellow (\033[33m)
    display_val = str(list(tool_input.values())[0])[:80] if tool_input else ""
    print(f"\033[33m[{tool_name}] {display_val}...\033[0m")
    
    # Routing Logic
    if tool_name in mcp_names:
        # Route to external MCP server
        output = await execute_mcp_tool(tool_name, tool_input)
    else:
        # Route to local core.py implementation
        handler = ASYNC_DISPATCH.get(tool_name)
        output = await handler(tool_input) if handler else f"Error: Unknown tool {tool_name}"
        
    # Print a snippet of the result
    print(str(output)[:200])
    return block.id, str(output)


# === Agent Loop with MCP Integration ===

async def agent_loop_mcp(messages: List[Dict[str, Any]], all_tool_definitions: List[Dict[str, Any]]) -> None:
    """
    The Thinking-Acting loop that utilizes both core and MCP tools.

    Args:
        messages (List[Dict[str, Any]]): The session history.
        all_tool_definitions (List[Dict[str, Any]]): Combined set of tool schemas.
    """
    # Create a lookup set for fast tool routing
    mcp_tool_names = set(MCP_TOOL_MAP.keys())
    
    # Update the agent persona to reflect MCP awareness
    system_prompt = (
        f"You are a coding agent at {os.getcwd()}. "
        "You have access to local tools and remote MCP tools. "
        "MCP tools are prefixed with mcp__<server>__<tool>. Use them for "
        "external services like GitHub, Google Search, or internal APIs."
    )
    
    while True:
        print("\n\033[36m> Thinking...\033[0m")
        
        # Define the synchronous stream call for the thread executor
        def _blocking_stream():
            with client.messages.stream(
                model=MODEL, 
                system=system_prompt, 
                messages=messages,
                tools=all_tool_definitions, 
                max_tokens=8000,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                return stream.get_final_message()
        
        # Execute the thinking turn
        response = await asyncio.get_event_loop().run_in_executor(None, _blocking_stream)
        print() # Newline
        
        # Save assistant message to context
        messages.append({"role": "assistant", "content": response.content})
        
        # Break if the model is done
        if response.stop_reason != "tool_use":
            return
            
        # Process tool calls in parallel using asyncio.gather
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        execution_tasks = [dispatch_one_tool(b, mcp_tool_names) for b in tool_blocks]
        
        results_pairs = await asyncio.gather(*execution_tasks)
        results_map = dict(results_pairs)
        
        # Assemble results for the next LLM turn
        turn_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": results_map[b.id]}
            for b in tool_blocks
        ]
        messages.append({"role": "user", "content": turn_results})


# === Main Runtime Execution ===

async def main() -> None:
    """
    Initializes the MCP environment, merges toolsets, and starts the REPL.
    """
    # UI Header
    print("\033[90ms21: MCP runtime | connecting servers from config/mcp_config.yaml...\033[0m")
    
    # 1. Boot-time MCP Server Discovery
    mcp_extra_tools = await connect_mcp_servers()
    
    # 2. Merge local core tools with discovered remote tools
    all_tools = EXTENDED_TOOLS + mcp_extra_tools
    
    # UI Summary
    print(f"\033[90m  Environment Ready: built-in={len(EXTENDED_TOOLS)} | MCP={len(mcp_extra_tools)} | total={len(all_tools)}\033[0m\n")

    history: List[Dict[str, Any]] = []
    loop = asyncio.get_event_loop()
    
    try:
        # Main Command-Line Loop
        while True:
            try:
                # Capture user input in a non-blocking thread
                query: str = await loop.run_in_executor(
                    None, lambda: input("\033[36ms21 >> \033[0m").strip()
                )
            except (EOFError, KeyboardInterrupt):
                break

            if not query or query.lower() in ("q", "exit", "quit"):
                break

            # Process the user turn
            history.append({"role": "user", "content": query})
            await agent_loop_mcp(history, all_tools)
            print()
            
    finally:
        # 3. Graceful Shutdown: Close all active MCP sessions
        print("\033[90m  [MCP] Shutting down active server sessions...\033[0m")
        for name, session in MCP_SESSIONS.items():
            try:
                # Signal the server to exit via the async context manager protocol
                await session.__aexit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    # Start the event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass