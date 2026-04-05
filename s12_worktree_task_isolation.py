#!/usr/bin/env python3
"""
s12_worktree_task_isolation.py: Implementation of Git Worktree Task Isolation.

Motto: "Each works in its own directory, no interference"

This module addresses the risks of parallel agent execution in a shared 
codebase. When multiple agents run simultaneously, they may attempt to edit 
the same files, resulting in corrupted states. This script solves this by 
assigning every task its own 'Git Worktree'.

Key Architectural Concepts:
    1. Git Worktree: A feature of Git that allows having multiple working 
       trees attached to the same repository, each checked out to a 
       different branch.
    2. Branch Isolation: Each task creates a unique branch (e.g., task/abc123). 
       This keeps the 'main' branch clean until work is verified.
    3. Directory Switching: The execution environment dynamically changes 
       directories (`os.chdir`) to ensure tool outputs (like file writes) 
       happen in the correct isolated path.
    4. Parallel Concurrency: Using Python's `threading` module to run 
       multiple agents in their respective worktrees at the same time.

Operational Flow:
    - Task Start -> Create Git Branch -> Create Worktree Directory.
    - Agent Execution -> Perform work inside the worktree path.
    - Task Completion -> Result extraction -> Cleanup (Remove worktree/branch).
"""

# === Standard Library Imports ===
import os          # Operating system interfaces (dir changes, pathing)
import json        # JSON serialization for task data
import subprocess  # Subprocess management for Git commands
import threading   # Support for parallel agent execution
import shutil      # High-level file operations (directory removal)
import sys         # System-specific parameters
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Tuple, Optional  # For strict type hinting

# === Local Module Imports ===
from core import (
    client,            # The configured Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file and shell tools
    EXTENDED_DISPATCH, # Mapping for standard tool implementations
    run_bash           # Synchronous bash tool from core.py
)

# === Git Utility Helpers ===

def _git(args: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """
    Executes a Git command and returns the result.

    Args:
        args (List[str]): List of arguments to pass to the git binary.
        cwd (str, optional): The directory to execute the command in.

    Returns:
        Tuple[int, str, str]: (Return code, stdout, stderr)
    """
    # Execute the command synchronously
    result = subprocess.run(
        ["git"] + args, 
        capture_output=True, 
        text=True, 
        cwd=cwd or os.getcwd()
    )
    # Return the execution code along with stripped output streams
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# === Worktree Management ===

def create_worktree(task_id: str) -> Tuple[str, str]:
    """
    Creates a new Git worktree and a corresponding branch for a task.

    Args:
        task_id (str): The unique identifier for the task.

    Returns:
        Tuple[str, str]: (The absolute path to the worktree, the branch name)
    """
    # Define a unique branch name for this specific task
    branch_name: str = f"task/{task_id}"
    
    # Define a path for the worktree outside of the main project root
    # e.g., ../.worktree-abc123
    worktree_path: str = str(Path(os.getcwd()).parent / f".worktree-{task_id[:8]}")
    
    # Pre-emptive Cleanup: If the directory exists from a crashed run, remove it
    if Path(worktree_path).exists():
        shutil.rmtree(worktree_path, ignore_errors=True)
        _git(["worktree", "remove", "--force", worktree_path])
        
    # Attempt to add the worktree with a new branch (-b)
    rc, out, err = _git(["worktree", "add", "-b", branch_name, worktree_path])
    
    if rc != 0:
        # If branch already exists (rare), delete branch and force create
        print(f"\033[33m  [worktree] Branch conflict. Resetting branch: {branch_name}\033[0m")
        _git(["branch", "-D", branch_name])
        _git(["worktree", "add", "-b", branch_name, worktree_path])
        
    return worktree_path, branch_name


def remove_worktree(path: str, branch: str) -> None:
    """
    Cleans up the task-specific worktree and deletes the temporary branch.

    Args:
        path (str): The path to the worktree directory.
        branch (str): The name of the temporary git branch.
    """
    # Forcefully remove the worktree from Git's internal tracking
    _git(["worktree", "remove", "--force", path])
    
    # Remove the physical directory if Git didn't clear it
    if Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)
        
    # Delete the local branch to keep the repo clean
    _git(["branch", "-D", branch])


# === Agent Execution in Isolation ===

def run_task_in_worktree(task: Dict[str, Any]) -> str:
    """
    Orchestrates the entire lifecycle of an agent working in an isolated worktree.

    Args:
        task (Dict[str, Any]): The task object containing ID and description.

    Returns:
        str: The final text output produced by the agent.
    """
    task_id: str = task["id"]
    description: str = task["description"]
    
    # Step 1: Initialize Worktree
    # Fallback to CWD if we are not currently in a Git repository
    if not Path(".git").exists() and not Path(os.getcwd()).parent.joinpath(".git").exists():
        print(f"\033[33m  [worktree] No Git repo detected. Running in CWD.\033[0m")
        wt_path, wt_branch = os.getcwd(), None
    else:
        try:
            wt_path, wt_branch = create_worktree(task_id)
            print(f"\033[32m  [worktree] Created isolated environment at: {wt_path}\033[0m")
        except Exception as e:
            print(f"\033[31m  [worktree] Setup failed: {e}. Falling back to CWD.\033[0m")
            wt_path, wt_branch = os.getcwd(), None

    # Step 2: Configure Persona for the isolated environment
    system_prompt: str = (
        f"You are a coding agent working in isolated directory: {wt_path}. "
        f"Goal: {description}. "
        "Any file changes you make are isolated in this directory's branch."
    )
    
    # Initialize interaction history for this specific worker
    messages: List[Dict[str, Any]] = [{"role": "user", "content": description}]

    try:
        # Autonomous Turn-Taking Loop
        while True:
            # Call the LLM to generate next thought/action
            response = client.messages.create(
                model=MODEL, 
                system=system_prompt, 
                messages=messages,
                tools=EXTENDED_TOOLS, 
                max_tokens=8000,
            )
            messages.append({"role": "assistant", "content": response.content})
            
            # Stop if the model is done or hits a non-tool exit
            if response.stop_reason != "tool_use":
                break
            
            # Process tool calls with strict directory context switching
            results: List[Dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                
                # Capture current working directory to restore later
                original_cwd = os.getcwd()
                
                try:
                    # Switch context to the worktree so bash/file commands execute there
                    os.chdir(wt_path)
                    
                    # Log the tool call in Yellow (\033[33m)
                    first_arg = str(list(block.input.values())[0])[:60]
                    print(f"\033[33m  [{task_id[:6]}][{block.name}] {first_arg}\033[0m")
                    
                    # Execute the handler
                    if block.name == "bash":
                        output = run_bash(block.input["command"])
                    else:
                        # Fetch handler from the extended dispatch map
                        handler = EXTENDED_DISPATCH.get(block.name, lambda _: "Error: Unknown tool")
                        output = handler(block.input)
                finally:
                    # ALWAYS restore the original directory even if the tool crashed
                    os.chdir(original_cwd)
                
                # Append tool result to the batch
                results.append({
                    "type": "tool_result", 
                    "tool_use_id": block.id, 
                    "content": str(output)
                })
            
            # Feed batch of results back to the model
            messages.append({"role": "user", "content": results})

        # Extract the final textual response
        return "".join(b.text for b in messages[-1]["content"] if hasattr(b, "text"))
        
    finally:
        # Step 3: Cleanup isolated environment
        if wt_branch:
            print(f"\033[90m  [worktree] Cleaning up {wt_path}...\033[0m")
            remove_worktree(wt_path, wt_branch)


# === Main Execution Block ===

def main() -> None:
    """
    Demonstrates the parallel execution of multiple tasks in isolated worktrees.
    """
    import uuid # For generating unique task IDs

    # Mock list of tasks that might conflict if run in the same directory
    demo_tasks: List[Dict[str, str]] = [
        {"id": uuid.uuid4().hex[:8], "description": "Count the number of Python files in the repo."},
        {"id": uuid.uuid4().hex[:8], "description": "List all TODO comments in Python files."},
    ]

    # UI Header
    print(f"\033[90ms12: worktree isolation | {len(demo_tasks)} parallel tasks\033[0m\n")

    # Storage for results indexed by task ID
    execution_results: Dict[str, str] = {}
    threads: List[threading.Thread] = []

    # Function to be executed by each thread
    def _thread_worker(task_obj: Dict[str, str]):
        result_text = run_task_in_worktree(task_obj)
        execution_results[task_obj["id"]] = result_text

    # Spawn a thread for each task
    for task in demo_tasks:
        t = threading.Thread(target=_thread_worker, args=(task,), daemon=True)
        threads.append(t)
        t.start()

    # Wait for all threads to finish execution
    for t in threads:
        t.join()

    # Final result summary
    print("\n\033[36mParallel Execution Results:\033[0m")
    for tid, res in execution_results.items():
        print(f"\n--- Task ID: {tid} ---")
        print(res)


if __name__ == "__main__":
    # Script entry point
    main()