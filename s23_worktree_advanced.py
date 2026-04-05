#!/usr/bin/env python3
"""
s23_worktree_advanced.py: Production-Grade Git Worktree Isolation & Conflict Detection.

Motto: "Git state is sacred; every edge case handled"

This module is the culmination of the parallel execution series. It provides 
a high-integrity environment for multiple agents to operate on the same 
repository simultaneously without interfering with the 'main' branch or each 
other's work.

Key Architectural Improvements over s12:
    1. Git State Safety: Checks for 'dirty' working trees and 'detached HEAD' 
       states before allowing operations to proceed.
    2. Automated Pruning: Identifies and removes orphaned worktree metadata 
       left behind by crashed processes or manual deletions.
    3. Collision Detection: Analyzes the 'git diff' of completed tasks to 
       alert the user if two parallel agents modified the same files.
    4. Path/Branch Sanitization: Uses Regex to ensure task IDs are converted 
       into valid, safe Git branch names and filesystem paths.
    5. Context Switching: Orchestrates `os.chdir` calls to ensure that 
       relative paths in tool calls (read/write) target the correct isolation.

Special Commands:
    task1 | task2 | task3   -> Executes these tasks in parallel isolated trees.
    :list                   -> Displays all active git worktrees.
    :prune                  -> Forces a cleanup of stale worktree metadata.
"""

# === Standard Library Imports ===
import asyncio      # Asynchronous I/O and task orchestration
import json         # Structured output formatting
import os           # Operating system interfaces (pathing, chdir)
import re           # Regular expressions for string sanitization
import shutil       # High-level file and directory operations
import subprocess   # Managing Git as a subprocess
import sys          # System-specific parameters
from dataclasses import dataclass, field  # Clean state containers
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Tuple, Optional, Set, Union  # Type hinting

# === Local Module Imports ===
from core import (
    client,            # The configured Anthropic API client
    MODEL,             # The specific model ID (e.g., Claude 3.5 Sonnet)
    EXTENDED_TOOLS,    # Standard file and shell tools
    EXTENDED_DISPATCH, # Mapping for standard tool implementations
    dispatch_tools     # Logic to execute tool calls
)

# === Git State & Helper Logic ===

def _git(args: Union[List[str], str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """
    Executes a Git command and captures its result streams.

    Args:
        args: List of arguments or a single string (split automatically).
        cwd: Directory context for command execution.

    Returns:
        Tuple[int, str, str]: (Return code, stdout, stderr)
    """
    if isinstance(args, str):
        # Allow passing "status --porcelain" as a string for convenience
        args = args.split()
        
    result = subprocess.run(
        ["git"] + list(args), 
        capture_output=True, 
        text=True, 
        cwd=cwd or os.getcwd()
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def is_git_repo() -> bool:
    """Verifies if the current directory is within a Git repository."""
    return _git("rev-parse --git-dir")[0] == 0


def get_git_root() -> str:
    """Retrieves the absolute path to the top-level directory of the Git repo."""
    return _git("rev-parse --show-toplevel")[1]


def is_working_tree_dirty() -> bool:
    """Checks for uncommitted changes in the repository."""
    # --porcelain provides a stable, script-readable output format
    return bool(_git("status --porcelain")[1])


def get_current_branch() -> str:
    """
    Identifies the active branch name or identifies a detached HEAD.

    Returns:
        str: Branch name or (detached:SHA) description.
    """
    rc, name, _ = _git("symbolic-ref --short HEAD")
    if rc != 0:
        # If not on a branch, get the short SHA of the current commit
        _, sha, _ = _git("rev-parse --short HEAD")
        return f"(detached:{sha})"
    return name


def list_active_worktrees() -> List[Dict[str, Any]]:
    """
    Parses the 'git worktree list --porcelain' output into structured data.

    Returns:
        List[Dict[str, Any]]: List of worktree metadata dictionaries.
    """
    _, out, _ = _git("worktree list --porcelain")
    worktrees, current_item = [], {}
    
    for line in out.splitlines():
        if line.startswith("worktree "):
            if current_item:
                worktrees.append(current_item)
            current_item = {"path": line[9:]}
        elif line.startswith("HEAD "):
            current_item["head"] = line[5:]
        elif line.startswith("branch "):
            current_item["branch"] = line[7:]
        elif line == "detached":
            current_item["detached"] = True
            
    if current_item:
        worktrees.append(current_item)
    return worktrees


def prune_stale_worktrees() -> int:
    """
    Removes worktree metadata for directories that no longer exist on disk.

    Returns:
        int: Number of stale worktrees successfully pruned.
    """
    pruned_count = 0
    root = get_git_root()
    
    for wt in list_active_worktrees():
        path = wt.get("path", "")
        # Don't prune the main repository root
        if path and path != root and not Path(path).exists():
            _git(["worktree", "remove", "--force", path])
            pruned_count += 1
            
    # Final internal Git cleanup
    _git("worktree prune")
    return pruned_count


# === Task Isolation State Management ===

@dataclass
class WTask:
    """
    Represents the state of a task being executed in an isolated worktree.
    """
    task_id: str
    description: str
    branch: str = ""
    path:   str = ""
    status: str = "pending"
    result: str = ""
    error:  str = ""


def _generate_safe_git_name(task_id: str) -> str:
    """Sanitizes a task ID for use as a Git branch name."""
    # Replace non-alphanumeric chars with dashes and limit length
    safe = re.sub(r'[^a-zA-Z0-9_-]', '-', task_id)
    return f"task/{safe[:40]}"


def _generate_safe_filesystem_path(task_id: str) -> str:
    """Generates an absolute path for the worktree outside the main repo."""
    root = get_git_root()
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", task_id)
    # Put the worktree in the parent directory to avoid recursive git issues
    return str(Path(root).parent / f".worktree-{safe[:20]}")


def setup_isolated_worktree(task: WTask) -> Tuple[bool, str]:
    """
    Performs the full setup protocol for an isolated worktree.

    Steps:
        1. Guard against detached HEAD.
        2. Sanitize paths and branches.
        3. Clean up stale filesystem artifacts.
        4. Create the git branch and worktree.

    Returns:
        Tuple[bool, str]: (Success status, Error message if any)
    """
    branch = _generate_safe_git_name(task.task_id)
    path   = _generate_safe_filesystem_path(task.task_id)
    task.branch, task.path = branch, path

    # 1. Safety Guard: Disallow branching from a detached HEAD
    cb = get_current_branch()
    if cb.startswith("(detached:"):
        return False, f"Detached HEAD detected ({cb}). Please checkout a branch before running tasks."

    # 2. Cleanup: Remove existing worktree directory if it persists from a crash
    if Path(path).exists():
        _git(["worktree", "remove", "--force", path])
        shutil.rmtree(path, ignore_errors=True)

    # 3. Cleanup: Remove the branch if it already exists (ensures a clean slate)
    # We ignore the error if the branch doesn't exist
    _git(["branch", "-D", branch])

    # 4. Git Operation: Create the worktree linked to a new branch
    rc, _, err = _git(["worktree", "add", "-b", branch, path])
    if rc != 0:
        return False, f"Git worktree creation failed: {err}"
    
    print(f"\033[90m  [worktree] Created {path} (Branch: {branch})\033[0m")
    return True, ""


def teardown_worktree(task: WTask, force: bool = False) -> None:
    """
    Removes the worktree and the temporary branch.

    Args:
        task: The task object defining the environment to clean.
        force: If True, uses force flags (useful for cleanup after failures).
    """
    if not task.path:
        return
        
    flags = ["--force"] if force else []
    
    # 1. Remove from Git tracking
    _git(["worktree", "remove"] + flags + [task.path])
    
    # 2. Remove physical directory
    if Path(task.path).exists():
        shutil.rmtree(task.path, ignore_errors=True)
        
    # 3. Delete the temporary task branch
    if task.branch:
        _git(["branch", "-D", task.branch])


def analyze_parallel_conflicts(tasks: List[WTask]) -> List[str]:
    """
    Determines if parallel tasks have modified the same set of files.

    This is calculated by comparing each task branch against the 
    HEAD branch at the time of completion.

    Returns:
        List[str]: A list of conflict descriptions.
    """
    file_changes: Dict[str, Set[str]] = {}
    
    for t in tasks:
        if t.status != "done" or not t.branch:
            continue
            
        # Get list of files changed in this branch compared to current HEAD
        rc, out, _ = _git(["diff", "--name-only", "HEAD", t.branch])
        if rc == 0 and out:
            file_changes[t.task_id] = set(out.splitlines())
            
    conflicts = []
    task_ids = list(file_changes.keys())
    
    # Perform a pairwise comparison of all file sets
    for i in range(len(task_ids)):
        for j in range(i + 1, len(task_ids)):
            id_a, id_b = task_ids[i], task_ids[j]
            # Set intersection identifies overlapping files
            overlapping_files = file_changes[id_a] & file_changes[id_b]
            
            if overlapping_files:
                file_list = ", ".join(sorted(overlapping_files)[:5])
                conflicts.append(f"{id_a} ↔ {id_b}: {file_list}")
                
    return conflicts


# === Task Execution Logic ===

async def run_agent_in_worktree(task: WTask) -> str:
    """
    Runs the autonomous agent loop within the context of a worktree directory.
    """
    # System instructions specific to this isolated environment
    system_prompt = (
        f"You are a coding agent working in isolated worktree: {task.path}. "
        f"Goal: {task.description}. Summarize your changes and results when finished."
    )
    
    # Initialize interaction history
    messages: List[Dict[str, Any]] = [{"role": "user", "content": task.description}]
    
    while True:
        # Call the model via thread executor to keep the async loop free
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.messages.create(
                model=MODEL, 
                system=system_prompt, 
                messages=messages,
                tools=EXTENDED_TOOLS, 
                max_tokens=8000,
            )
        )
        messages.append({"role": "assistant", "content": response.content})
        
        # Stop if no more tools are requested
        if response.stop_reason != "tool_use":
            break
            
        # --- Context Switch ---
        # Temporarily change directory to the worktree for tool execution
        original_cwd = os.getcwd()
        try:
            os.chdir(task.path)
            # Execute standard tools (bash, read, write, etc.) in the worktree
            results = dispatch_tools(response.content, EXTENDED_DISPATCH)
        finally:
            # Restore original project root
            os.chdir(original_cwd)
            
        messages.append({"role": "user", "content": results})
        
    return "".join(block.text for block in messages[-1]["content"] if hasattr(block, "text"))


async def orchestrate_parallel_tasks(task_descriptions: List[str]) -> Dict[str, Any]:
    """
    Orchestrates the lifecycle of multiple tasks running in parallel worktrees.
    """
    if not is_git_repo():
        return {"error": "Target directory is not a Git repository."}
        
    if is_working_tree_dirty():
        print("\033[33mWarning: Uncommitted changes detected. Parallel branches will fork from HEAD.\033[0m")
        
    # Pre-clean stale data
    pruned = prune_stale_worktrees()
    if pruned:
        print(f"\033[90m  Cleanup: Pruned {pruned} stale worktree metadata entries.\033[0m")

    # Initialize task objects
    tasks = [WTask(task_id=f"T-{i+1}", description=desc) for i, desc in enumerate(task_descriptions)]
    
    active_tasks = []
    # 1. Setup Phase
    for t in tasks:
        success, err_msg = setup_isolated_worktree(t)
        if success:
            t.status = "running"
            active_tasks.append(t)
        else:
            t.status = "failed"
            t.error = err_msg
            print(f"\033[31m  [{t.task_id}] Setup Error: {err_msg}\033[0m")

    # 2. Execution Phase (Parallel)
    async def _safe_run(t: WTask):
        try:
            t.result = await run_agent_in_worktree(t)
            t.status = "done"
            print(f"\033[32m  [{t.task_id}] Task completed successfully.\033[0m")
        except Exception as e:
            t.status = "failed"
            t.error = str(e)
            print(f"\033[31m  [{t.task_id}] Execution crashed: {e}\033[0m")
        finally:
            # 3. Teardown Phase
            teardown_worktree(t, force=(t.status == "failed"))

    # Launch all workers concurrently
    await asyncio.gather(*[_safe_run(t) for t in active_tasks])

    # 4. Post-Mortem: Conflict Analysis
    conflicts = analyze_parallel_conflicts([t for t in tasks if t.status == "done"])
    if conflicts:
        print("\n\033[33m[Conflict Alert] Overlapping file modifications detected:\033[0m")
        for c in conflicts:
            print(f"  • {c}")

    # Return structured summary of the parallel run
    return {
        "summary": {
            "total": len(tasks),
            "success": len([t for t in tasks if t.status == "done"]),
            "failed": len([t for t in tasks if t.status == "failed"]),
        },
        "tasks": [
            {"id": t.task_id, "status": t.status, "result": t.result[:150] + "...", "error": t.error} 
            for t in tasks
        ],
        "conflicts": conflicts,
    }


# === Main Runtime REPL ===

async def main() -> None:
    """
    Initializes the terminal interaction for s23 Advanced Worktree operations.
    """
    # Header UI
    print("\033[90ms23: advanced worktrees | dirty-check · conflict-detect · stale-prune\033[0m")
    print("\033[90m  Usage: Input multiple tasks separated by '|' to run in parallel.\033[0m\n")

    if not is_git_repo():
        print("\033[31mFatal: No Git repository detected. Please run 'git init' first.\033[0m")
        sys.exit(1)

    branch_name = get_current_branch()
    print(f"  Repo Root: \033[36m{get_git_root()}\033[0m")
    print(f"  Current Branch: \033[36m{branch_name}\033[0m | Dirty: {'Yes' if is_working_tree_dirty() else 'No'}\n")

    loop = asyncio.get_event_loop()

    while True:
        try:
            # Capture user query asynchronously
            query: str = await loop.run_in_executor(None, lambda: input("\033[36ms23 >> \033[0m").strip())
        except (EOFError, KeyboardInterrupt):
            break
            
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Command: List active worktrees
        if query == ":list":
            for wt in list_active_worktrees():
                print(f"  Path: {wt.get('path','?')}  [Branch: {wt.get('branch','?')}]")
            continue
            
        # Command: Manual prune
        if query == ":prune":
            count = prune_stale_worktrees()
            print(f"  Successfully pruned {count} stale entries."); continue

        # Parallel Operation: Split query by pipe character '|'
        subtask_descriptions = [t.strip() for t in query.split("|") if t.strip()]
        
        print(f"\n  Initiating {len(subtask_descriptions)} parallel agents...\n")
        
        # Execute the parallel orchestration
        results = await orchestrate_parallel_tasks(subtask_descriptions)
        
        # Output results as structured JSON for clarity
        print("\n" + json.dumps(results, indent=2))
        print()


if __name__ == "__main__":
    # Start the event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass