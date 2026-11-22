"""Reviewer: checks evidence, flags speculation. It can never add findings."""
from __future__ import annotations

import json

from repo_doctor.core.models import Finding, Verification
from repo_doctor.providers import ProviderError
from repo_doctor.security.redaction import redact

from .base import AgentContext, extract_json

MAX_REVIEWED = 30
CONTEXT_LINES = 6

SYSTEM = (
    "You are a skeptical reviewer of automated code-analysis findings. For each finding decide: "
    "'confirm' (the quoted code supports the claim), 'speculative' (plausible but not shown by the code), "
    "or 'reject' (wrong, unsupported, or trivial). Also say whether the suggested fix is reasonable and safe. "
    "Judge ONLY from the code excerpt provided. Reply with ONLY JSON: "
    '{"reviews": [{"id": "...", "verdict": "confirm|speculative|reject", "fix_ok": true, "reason": "..."}]}'
)


def check_evidence(ctx: AgentContext, f: Finding) -> Finding:
    """Deterministic validation: file must exist in the inventory; line must exist in file."""
    if f.file_path is None or f.verification == Verification.VERIFIED:
        return f  # deterministic findings are built from real scans (may name excluded files)
    if ctx.reader.normalize(f.file_path) is None:
        return f.model_copy(update={"verification": Verification.REJECTED,
                                    "review_note": "File path does not exist in the scanned inventory."})
    n = len(ctx.reader.lines(f.file_path))
    if f.line is not None and not 1 <= f.line <= n:
        return f.model_copy(update={"line": None, "confidence": min(f.confidence, 0.3),
                                    "review_note": f"Line out of range (file has {n} lines); line dropped."})
    return f


def _excerpt(ctx: AgentContext, f: Finding) -> str:
    if not f.file_path:
        return "(project-level finding)"
    lines = ctx.reader.lines(f.file_path)
    centre = f.line or 1
    lo, hi = max(1, centre - CONTEXT_LINES), min(len(lines), centre + CONTEXT_LINES)
    return "\n".join(f"{i:>5}| {lines[i - 1][:300]}" for i in range(lo, hi + 1))


class ReviewerAgent:
    name = "reviewer"

    def review(self, ctx: AgentContext, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """Returns (kept, rejected)."""
        checked = [check_evidence(ctx, f) for f in findings]
        pending = [f for f in checked if f.verification in (Verification.SUPPORTED, Verification.UNVERIFIED)]
        verdicts: dict[str, dict] = {}
        if ctx.llm_enabled and pending:
            pending = pending[:MAX_REVIEWED]
            payload = [{k: redact(v) if isinstance(v, str) else v for k, v in {
                "id": f.id, "title": f.title, "claim": f.description[:400], "severity": f.severity.value,
                "suggested_fix": f.suggested_fix[:300], "evidence": f.evidence[:300],
                "file": f.file_path, "line": f.line, "code": _excerpt(ctx, f)}.items()} for f in pending]
            try:
                ctx.llm_calls += 1
                data = extract_json(ctx.provider.complete(SYSTEM, json.dumps(payload)))  # type: ignore[union-attr]
                if data and isinstance(data.get("reviews"), list):
                    verdicts = {str(r.get("id")): r for r in data["reviews"] if isinstance(r, dict)}
                else:
                    ctx.warnings.append("reviewer: model returned invalid JSON; LLM review skipped.")
            except ProviderError as exc:
                ctx.warnings.append(f"reviewer: LLM review failed: {exc}")
            if len(checked) and len([f for f in checked if f.verification in (Verification.SUPPORTED, Verification.UNVERIFIED)]) > MAX_REVIEWED:
                ctx.notes.append(f"reviewer: only the first {MAX_REVIEWED} unverified findings were LLM-reviewed.")

        kept, rejected = [], []
        for f in checked:
            v = verdicts.get(f.id)
            if v:
                verdict, reason = str(v.get("verdict", "")).lower(), redact(str(v.get("reason", "")))[:300]
                upd: dict = {"review_note": reason}
                if verdict == "reject":
                    upd["verification"] = Verification.REJECTED
                elif verdict == "speculative":
                    upd.update(verification=Verification.SPECULATIVE, confidence=round(f.confidence * 0.5, 2))
                if v.get("fix_ok") is False:
                    upd["suggested_fix"] = ""
                    upd["review_note"] = (reason + " Suggested fix removed by reviewer as unreasonable.").strip()
                f = f.model_copy(update=upd)
            (rejected if f.verification == Verification.REJECTED else kept).append(f)
        return kept, rejected
