from __future__ import annotations

from difflib import SequenceMatcher

from .models import Finding, Verification

_RANK = {
    Verification.VERIFIED: 3, Verification.SUPPORTED: 2, Verification.UNVERIFIED: 1,
    Verification.SPECULATIVE: 0, Verification.REJECTED: -1,
}


def _is_tool(f: Finding) -> bool:
    return "tool:" in f.agent


def _similar(a: Finding, b: Finding) -> bool:
    if a.file_path != b.file_path:
        return False
    # A tool and a built-in check flagging the same line in the same area are the same issue.
    if (a.file_path and a.line is not None and a.line == b.line and _is_tool(a) != _is_tool(b)
            and a.category.split("/")[0] == b.category.split("/")[0]):
        return True
    if a.line is not None and b.line is not None:
        if abs(a.line - b.line) > 2:
            return False
        if a.line == b.line and a.category == b.category:
            return True
    return SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio() >= 0.6


def deduplicate(findings: list[Finding]) -> tuple[list[Finding], int]:
    """Merge near-identical findings, keeping the best-evidenced one. Returns (result, merged)."""
    kept: list[Finding] = []
    merged = 0
    for f in findings:
        for i, k in enumerate(kept):
            if _similar(f, k):
                winner, other = (f, k) if (_RANK[f.verification], f.confidence) > (
                    _RANK[k.verification], k.confidence) else (k, f)
                agents = sorted(set(winner.agent.split(", ")) | set(other.agent.split(", ")))
                kept[i] = winner.model_copy(update={"agent": ", ".join(agents)})
                merged += 1
                break
        else:
            kept.append(f)
    return kept, merged
