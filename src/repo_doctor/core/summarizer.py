"""LLM-written prose for the overview. Optional: the report works without it."""
from __future__ import annotations

from pathlib import Path

from repo_doctor.core.context import build_overview_context
from repo_doctor.core.models import ProjectProfile
from repo_doctor.providers import LLMProvider

SYSTEM = (
    "You are a software architect summarising a code repository for a developer. "
    "Use ONLY the facts and file excerpts provided. If something is not evident, say so. "
    "Do not invent file names, modules or features. Be concise and use plain language."
)


def summarize_architecture(provider: LLMProvider, root: Path, profile: ProjectProfile, simple: bool = False) -> str:
    style = (
        "Explain it to a beginner in simple language (no jargon), 150-250 words."
        if simple
        else "Write two short sections: '### Project description' (2-3 sentences) and "
        "'### Architecture overview' (a short bullet list of the main components and how they relate)."
    )
    prompt = f"{style}\n\nRepository facts:\n\n{build_overview_context(root, profile)}"
    return provider.complete(SYSTEM, prompt).strip()
