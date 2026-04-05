#!/usr/bin/env python3
"""
s05_skill_loading.py: Implementation of On-Demand Knowledge Retrieval.

Motto: "Load knowledge when you need it, not upfront"

This module introduces a 'Meta-Tooling' approach to solve the "Context Window 
Bloat" problem. Instead of stuffing every possible instruction, guideline, 
or specialized SOP (Standard Operating Procedure) into the System Prompt, 
this script allows the agent to 'discover' and 'load' specific skills 
as needed.

Key Architectural Concepts:
    1. Discovery: The agent is given a lightweight index of available skills 
       (Names and 1-line descriptions) in its system prompt.
    2. Lazy Loading: The full documentation for a skill is only injected 
       into the conversation when the agent explicitly calls `load_skill`.
    3. Context Efficiency: This allows the agent to have access to hundreds 
       of specialized skills without exceeding token limits or confusing the 
       model with irrelevant data.

Skill Structure:
    Skills are stored in: skills/<skill_name>/SKILL.md
"""

# === Standard Library Imports ===
import os      # Operating system interfaces
import sys     # System-specific parameters and functions
from pathlib import Path  # Object-oriented filesystem paths
from typing import List, Dict, Any, Union, Optional  # For strict type hinting

# === Local Module Imports ===
from core import (
    EXTENDED_TOOLS,      # Standard file/shell tools (bash, read, etc.)
    EXTENDED_DISPATCH,   # Mapping for standard tools
    stream_loop          # The core autonomous loop logic
)

# === Configuration and Constants ===

# Define the absolute path to the 'skills' repository directory.
# This assumes the directory structure: project_root/skills/
SKILLS_DIR: Path = Path(__file__).parent.parent / "skills"

# === Skill Discovery Logic ===

def discover_skills() -> Dict[str, str]:
    """
    Scans the skills directory and extracts metadata from SKILL.md files.

    It parses the first non-empty line of text (ignoring YAML frontmatter) 
    to use as a brief description for the agent's index.

    Returns:
        Dict[str, str]: A dictionary mapping {skill_name: short_description}.
    """
    skills: Dict[str, str] = {}
    
    # Ensure the skills directory actually exists to avoid iteration errors
    if not SKILLS_DIR.exists():
        return skills

    # Iterate through subdirectories in alphabetical order
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        
        # We only consider directories that contain a SKILL.md file
        if skill_dir.is_dir() and skill_md.exists():
            try:
                # Read file and split into lines for parsing
                lines = skill_md.read_text(encoding="utf-8").splitlines()
                description = "No description available."
                in_frontmatter = False
                
                # Logic to find the first relevant line of descriptive text
                for line in lines:
                    stripped = line.strip()
                    # Toggle frontmatter state (skipping YAML headers)
                    if stripped == "---":
                        in_frontmatter = not in_frontmatter
                        continue
                    
                    # Ignore empty lines, headers (#), and frontmatter content
                    if not in_frontmatter and stripped and not stripped.startswith("#"):
                        description = stripped[:100]  # Cap length for prompt brevity
                        break
                
                skills[skill_dir.name] = description
            except Exception as e:
                # Log error and continue to the next skill
                skills[skill_dir.name] = f"Error reading metadata: {e}"
                
    return skills


def run_list_skills() -> str:
    """
    Formats the list of discovered skills for the agent's tool output.

    Returns:
        str: A formatted string list of available skills.
    """
    skills = discover_skills()
    if not skills:
        return "(no skills found in skills/ directory)"
    
    # Format as a bulleted list for the LLM's consumption
    return "\n".join(f"  - {name}: {desc}" for name, desc in skills.items())


def run_load_skill(name: str) -> str:
    """
    Loads the full content of a specific skill file into the context.

    Args:
        name (str): The folder name of the skill to load.

    Returns:
        str: The full text content of the skill, or an error message.
    """
    # Sanitize and build the path to the skill file
    skill_path = SKILLS_DIR / name / "SKILL.md"
    
    # Check for existence and potential directory traversal attempts
    if not skill_path.exists():
        return f"Error: skill '{name}' not found. Use list_skills to see valid names."
    
    try:
        # Load the full documentation
        content = skill_path.read_text(encoding="utf-8")
        return f"=== SKILL: {name} ===\n\n{content}\n\n=== END SKILL ==="
    except Exception as e:
        return f"Error loading skill '{name}': {e}"


# === Dynamic System Prompt Construction ===

# 1. Discover skills at startup to populate the system prompt
_initial_skills: Dict[str, str] = discover_skills()
_skill_index_str: str = "\n".join(
    f"  - {n}: {d}" for n, d in _initial_skills.items()
) or "  (none currently installed)"

# 2. Build the persona prompt with explicit instructions on skill loading
SYSTEM: str = (
    f"You are a coding agent at {os.getcwd()}.\n"
    "You have access to specialized 'Skills' (domain knowledge files). "
    "When a task requires specific knowledge (e.g., a specific framework, "
    "API, or language), call load_skill(name) to get full instructions. "
    "Do NOT guess or hallucinate details if a skill is available.\n\n"
    f"Available Skills Index:\n{_skill_index_str}"
)

# === Tool Schema and Dispatch Extensions ===

# Define the meta-tools used for managing knowledge
SKILL_TOOLS: List[Dict[str, Any]] = EXTENDED_TOOLS + [
    {
        "name": "list_skills",
        "description": "List all available specialized skills with their descriptions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "load_skill",
        "description": (
            "Load the full instructions for a skill into your context. "
            "Use this before starting a task requiring specialized domain knowledge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string", 
                    "description": "The exact name of the skill folder to load."
                }
            },
            "required": ["name"],
        },
    },
]

# Map the meta-tools to their execution logic
SKILL_DISPATCH: Dict[str, Any] = {
    **EXTENDED_DISPATCH, # Inherit bash, read, write, etc.
    "list_skills": lambda inp: run_list_skills(),
    "load_skill":  lambda inp: run_load_skill(inp["name"]),
}


# === Main Execution Block ===

def main() -> None:
    """
    Initializes the terminal interaction for the s05 'Skill Loading' agent.
    """
    # Header display in Gray
    print("\033[90ms05: on-demand skill loading | list_skills · load_skill\033[0m\n")
    
    # Session interaction history
    history: List[Dict[str, Any]] = []

    # Main REPL loop
    while True:
        try:
            # User Input in Cyan
            query: str = input("\033[36ms05 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            # Graceful exit
            print("\nExiting session.")
            sys.exit(0)

        # Standard exit handlers
        if not query or query.lower() in ("q", "exit", "quit"):
            break

        # Record user query
        history.append({"role": "user", "content": query})
        
        # Execute the agentic loop with dynamic skill loading capabilities
        # and the custom system prompt containing the skill index.
        stream_loop(
            messages=history,
            tools=SKILL_TOOLS,
            dispatch=SKILL_DISPATCH,
            system=SYSTEM
        )
        
        # Visual spacer for next turn
        print()


if __name__ == "__main__":
    # Script entry point
    main()