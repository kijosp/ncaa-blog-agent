"""Prompt loader for blog search agent."""

import os

_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def load_prompt(name: str) -> str:
    """Load a prompt text file by name (without .txt extension)."""
    path = os.path.join(_PROMPTS_DIR, f"{name}.txt")
    with open(path) as f:
        return f.read()
