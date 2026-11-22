from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from repo_doctor import __version__
from repo_doctor.agents.base import AgentContext
from repo_doctor.analyzers.project import build_profile
from repo_doctor.analyzers.repository import scan_repository
from repo_doctor.analyzers.static_analysis import SPECS, ToolSuite, detect_tools
from repo_doctor.core.fixer import (
    MAX_SELECTED,
    Hooks,
    Proposal,
    load_findings,
    run_fix_session,
    select_findings,
)
from repo_doctor.core.models import ProjectProfile, Severity
from repo_doctor.core.orchestrator import Orchestrator
from repo_doctor.core.settings import (
    API_KEY_ENV,
    DEFAULT_MODELS,
    SUPPORTED_PROVIDERS,
    ConfigError,
    Settings,
    get_api_key,
    load_settings,
    save_api_key,
    save_settings,
)
from repo_doctor.core.summarizer import summarize_architecture
from repo_doctor.core.verify import Verifier
from repo_doctor.providers import ProviderError, create_provider
from repo_doctor.reports.fix_report import FIX_REPORT_NAME, render_fix_report
from repo_doctor.reports.health import REPORT_NAMES, render_health
from repo_doctor.reports.markdown import OVERVIEW_NAME, render_overview, write_report
from repo_doctor.security.permissions import ApprovalPolicy, PathSecurityError, resolve_repo_root
from repo_doctor.tools.file_reader import RepoReader

app = typer.Typer(help="AI-powered repository analysis agent.", no_args_is_help=True,
                  add_completion=False)
console = Console()
err = Console(stderr=True)

PathOpt = Annotated[Path, typer.Option("--path", "-p", help="Repository to analyse.")]
OutOpt = Annotated[Path | None, typer.Option("--out", help="Report directory (default: ./reports).")]
NoLLMOpt = Annotated[bool, typer.Option("--no-llm", help="Skip AI calls; deterministic output only.")]
TrustOpt = Annotated[bool, typer.Option(
    "--trust-tools", help="Run detected analysis tools without asking (trusted execution mode).")]
NoToolsOpt = Annotated[bool, typer.Option("--no-tools", help="Do not run external analysis tools.")]


def _fail(msg: str) -> typer.Exit:
    err.print(f"[red]Error:[/red] {msg}")
    return typer.Exit(1)


def _settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        raise _fail(str(exc))


def _root(path: Path) -> Path:
    try:
        return resolve_repo_root(path)
    except PathSecurityError as exc:
        raise _fail(str(exc))


def _profile(root: Path, settings: Settings) -> ProjectProfile:
    with console.status("Scanning repository..."):
        inv = scan_repository(root, settings.scan)
        return build_profile(root, inv)


def _narrative(settings: Settings, root: Path, profile: ProjectProfile, simple=False) -> str | None:
    """Return an AI summary, or None (with a warning) if it can't be produced."""
    try:
        provider = create_provider(settings)
    except ProviderError as exc:
        err.print(f"[yellow]AI summary skipped:[/yellow] {exc}")
        return None
    console.print(
        f"[dim]Sending a redacted summary of the repo (README, manifests, entry points) "
        f"to {provider.name} ({provider.model}).[/dim]"
    )
    try:
        with console.status("Asking the model..."):
            return summarize_architecture(provider, root, profile, simple)
    except ProviderError as exc:
        err.print(f"[yellow]AI summary failed:[/yellow] {exc}")
        return None


@app.callback(invoke_without_command=True)
def _root_cb(version: Annotated[bool, typer.Option("--version", help="Show version.")] = False):
    if version:
        console.print(f"repo-doctor {__version__}")
        raise typer.Exit()


@app.command()
def setup(
    provider: Annotated[str | None, typer.Option(help="anthropic or openai")] = None,
    model: Annotated[str | None, typer.Option(help="Model name")] = None,
    skip_check: Annotated[bool, typer.Option("--skip-check", help="Don't test connectivity.")] = False,
):
    """Create config and configure an AI provider."""
    import sys
    if sys.version_info < (3, 11):  # noqa: UP036 - runtime guard for odd installs
        raise _fail(f"Python 3.11+ required, found {sys.version.split()[0]}")
    console.print(f"[green]✓[/green] Python {sys.version.split()[0]}")
    import importlib.util
    missing = [m for m in ("httpx", "pathspec", "pydantic", "rich") if not importlib.util.find_spec(m)]
    if missing:
        raise _fail(f"Missing dependencies: {', '.join(missing)}. Run `pip install -e .`")
    console.print("[green]✓[/green] Dependencies installed")

    console.print("Analysis tools (optional; run only with your approval):")
    hints = {sp.name: sp.install_hint for sp, _, _ in SPECS}
    for tname, found in detect_tools().items():
        if found:
            console.print(f"  [green]✓[/green] {tname}")
        else:
            console.print(f"  [yellow]-[/yellow] {tname} not found. Install: {hints[tname]}")

    settings = _settings()
    provider = provider or typer.prompt(
        f"AI provider ({'/'.join(SUPPORTED_PROVIDERS)})", default=settings.llm.provider)
    if provider not in SUPPORTED_PROVIDERS:
        raise _fail(f"Unsupported provider '{provider}'. Choose: {', '.join(SUPPORTED_PROVIDERS)}")
    model = model or typer.prompt("Model name", default=settings.llm.model or DEFAULT_MODELS[provider])
    settings.llm.provider, settings.llm.model = provider, model

    env = API_KEY_ENV[provider]
    if get_api_key(provider):
        console.print(f"[green]✓[/green] API key found ({env} or stored credentials)")
    else:
        console.print(
            f"No API key found. Note: an API key is separate from a Claude/ChatGPT subscription.\n"
            f"Either export [bold]{env}[/bold], or enter it now to store it in a private (0600) file."
        )
        key = typer.prompt("API key (leave empty to skip)", default="", hide_input=True,
                           show_default=False)
        if key.strip():
            path = save_api_key(provider, key.strip())
            console.print(f"[green]✓[/green] Key stored in {path}")

    path = save_settings(settings)
    console.print(f"[green]✓[/green] Config written to {path}")

    if skip_check:
        return
    try:
        prov = create_provider(settings)
        with console.status(f"Testing connection to {provider}..."):
            prov.check_connection()
        console.print(f"[green]✓[/green] Connected to {provider} ({prov.model})")
    except ProviderError as exc:
        err.print(f"[yellow]![/yellow] Connectivity check failed: {exc}")
        err.print("Config was saved. Fix the issue and re-run `repo-doctor setup`.")
        raise typer.Exit(1)


def _confirm(message: str) -> bool:
    """Interactive approval; any failure to ask (no TTY/EOF) means NO."""
    try:
        return typer.confirm(message, default=False)
    except (typer.Abort, EOFError):
        return False


def _run_mode(mode: str, path: Path, out: Path | None, no_llm: bool,
              trust_tools: bool = False, no_tools: bool = False) -> None:
    settings = _settings()
    root = _root(path)
    out_dir = out or Path("reports")
    profile = _profile(root, settings)
    reader = RepoReader(root, profile.inventory)

    provider = None
    if not no_llm:
        try:
            provider = create_provider(settings)
        except ProviderError as exc:
            err.print(f"[yellow]LLM analysis skipped:[/yellow] {exc}")
            err.print("[yellow]Running deterministic checks only.[/yellow]")
    if provider:
        console.print(
            f"[dim]Notice: redacted code excerpts (up to {settings.agents.max_llm_calls_per_agent} "
            f"batches of ~{settings.agents.batch_chars} chars per agent) will be sent to "
            f"{provider.name} ({provider.model}). Use --no-llm to avoid this.[/dim]")
    ctx = AgentContext(root=root, profile=profile, reader=reader, settings=settings, provider=provider)
    suite = None
    if not no_tools:
        policy = ApprovalPolicy(trusted=trust_tools or settings.tools.trusted, prompt=_confirm)
        suite = ToolSuite(policy, settings.tools)
    orch = Orchestrator(ctx, tools=suite)
    total = orch.total_steps(mode)
    counter = {"n": 0}

    def progress(step: str, state: str) -> None:
        if state == "running":
            return
        counter["n"] += 1
        label = {"static": "Static Analysis", "code": "Code Analysis Agent", "security": "Security Agent",
                 "testing": "Testing Agent", "reviewer": "Reviewer Agent"}.get(step, step)
        mark = "[green]DONE[/green]" if state == "done" else "[red]FAILED[/red]"
        console.print(f"[{counter['n']}/{total}] {label:<24} {mark}")

    console.print(f"[bold]Repository:[/bold] {profile.name}   "
                  f"[bold]Languages:[/bold] {', '.join(profile.languages) or '-'}")
    # Not wrapped in a spinner: tool approval prompts need a clean terminal.
    result = orch.run(mode, progress)
    result.root = str(root)

    narrative = None
    if mode == "analyze":
        write_report(render_overview(profile, None), out_dir, OVERVIEW_NAME)
        if provider:
            narrative = _narrative(settings, root, profile)
            if narrative:
                write_report(render_overview(profile, narrative), out_dir, OVERVIEW_NAME)
            result.llm_calls += 1 if narrative else 0
    report_path = write_report(render_health(profile, result, narrative), out_dir, REPORT_NAMES[mode])
    write_report(result.model_dump_json(indent=2), out_dir, "findings.json")

    c = result.severity_counts()
    table = Table(show_header=False, box=None)
    for label, key in (("Critical Issues", "critical"), ("High Issues", "high"),
                       ("Medium Issues", "medium"), ("Low Issues", "low")):
        table.add_row(label + ":", str(c[key]))
    console.print(Panel(table, title="RESULTS"))
    console.print(f"Project Health: [bold]{result.health()}[/bold]")
    if result.tools:
        tt = Table("Tool", "Status", "Details", box=None, pad_edge=False)
        colors = {"ran": "green", "skipped": "yellow", "denied": "yellow", "failed": "red"}
        for t in result.tools:
            tt.add_row(t.name, f"[{colors.get(t.status, 'white')}]{t.status}[/]", t.detail)
        console.print(tt)
    for name, msg in result.agent_errors.items():
        err.print(f"[red]Agent '{name}' failed:[/red] {msg}")
    for note in result.notes:
        if note.startswith("WARNING"):
            err.print(f"[yellow]{note}[/yellow]")
    console.print(f"Report generated: [bold]{report_path}[/bold]")
    if result.agent_errors and len(result.agent_errors) >= total - 1:
        raise typer.Exit(1)


@app.command()
def analyze(path: PathOpt = Path("."), out: OutOpt = None, no_llm: NoLLMOpt = False,
    trust_tools: TrustOpt = False, no_tools: NoToolsOpt = False):
    """Full analysis: overview + code, security and testing agents + reviewer."""
    _run_mode("analyze", path, out, no_llm, trust_tools, no_tools)


@app.command()
def security(path: PathOpt = Path("."), out: OutOpt = None, no_llm: NoLLMOpt = False,
    trust_tools: TrustOpt = False, no_tools: NoToolsOpt = False):
    """Security-focused analysis."""
    _run_mode("security", path, out, no_llm, trust_tools, no_tools)


@app.command()
def quality(path: PathOpt = Path("."), out: OutOpt = None, no_llm: NoLLMOpt = False,
    trust_tools: TrustOpt = False, no_tools: NoToolsOpt = False):
    """Code quality analysis."""
    _run_mode("quality", path, out, no_llm, trust_tools, no_tools)


@app.command()
def tests(path: PathOpt = Path("."), out: OutOpt = None, no_llm: NoLLMOpt = False,
    trust_tools: TrustOpt = False, no_tools: NoToolsOpt = False):
    """Testing analysis (does not execute tests)."""
    _run_mode("tests", path, out, no_llm, trust_tools, no_tools)


@app.command()
def explain(path: PathOpt = Path(".")):
    """Explain the project architecture in simple language (requires an AI provider)."""
    settings = _settings()
    root = _root(path)
    profile = _profile(root, settings)
    text = _narrative(settings, root, profile, simple=True)
    if text is None:
        raise _fail("Could not generate an explanation. Run `repo-doctor setup` or check the error above.")
    console.print(Panel(text, title=f"About {profile.name}"))


@app.command()
def fix(
    path: PathOpt = Path("."),
    out: OutOpt = None,
    ids: Annotated[list[str] | None, typer.Option("--id", help="Finding id to fix (repeatable), e.g. F-003.")] = None,
    min_severity: Annotated[str | None, typer.Option(help="Only findings at or above: critical|high|medium|low")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show proposed diffs only; change nothing.")] = False,
    run_tests: Annotated[bool, typer.Option("--run-tests", help="Also run the project's tests to verify fixes (asks first).")] = False,
    trust_tools: TrustOpt = False,
    no_tools: NoToolsOpt = False,
):
    """Propose fixes for findings; apply ONLY the ones you approve, then verify (roll back on failure)."""
    from rich.syntax import Syntax
    settings = _settings()
    root = _root(path)
    out_dir = out or Path("reports")
    try:
        result = load_findings(out_dir)
    except FileNotFoundError as exc:
        raise _fail(str(exc))
    if result.root and Path(result.root).resolve() != root:
        raise _fail(f"{out_dir}/findings.json was generated for {result.root}, not {root}. "
                    "Re-run `repo-doctor analyze` for this repository (or pass the matching --path).")
    sev = None
    if min_severity:
        try:
            sev = Severity(min_severity.lower())
        except ValueError:
            raise _fail(f"Unknown severity '{min_severity}'")

    fixable, unfixable = select_findings(result, ids or [], sev)
    unknown = {i.upper() for i in (ids or [])} - {f.id.upper() for f in result.findings}
    if unknown:
        err.print(f"[yellow]Unknown finding id(s): {', '.join(sorted(unknown))}[/yellow]")
    if unfixable and not ids:
        console.print(f"[dim]{len(unfixable)} finding(s) are not auto-fixable (secrets, dependency issues, missing "
                      "tests, project-level, speculative). They stay in the report for manual action.[/dim]")
    for f, why in unfixable:
        if ids:
            err.print(f"[yellow]{f.id} not auto-fixable:[/yellow] {why}")
    if not fixable:
        console.print("Nothing to fix.")
        return

    if not ids:
        table = Table("#", "ID", "Severity", "Location", "Title")
        for n, f in enumerate(fixable, 1):
            table.add_row(str(n), f.id, f.severity.value, f"{f.file_path}:{f.line}", f.title[:70])
        console.print(table)
        answer = typer.prompt("Fix which? (numbers/ids comma-separated, 'all', or empty to cancel)",
                              default="", show_default=False).strip().lower()
        if not answer:
            console.print("Cancelled. Nothing changed.")
            return
        if answer != "all":
            picks = {a.strip() for a in answer.split(",") if a.strip()}
            fixable = [f for n, f in enumerate(fixable, 1) if str(n) in picks or f.id.lower() in picks]
        if not fixable:
            raise _fail("No matching findings selected.")
    if len(fixable) > MAX_SELECTED:
        err.print(f"[yellow]Limiting to the first {MAX_SELECTED} findings per session.[/yellow]")

    try:
        provider = create_provider(settings)
    except ProviderError as exc:
        raise _fail(f"fix requires an AI provider to draft changes: {exc}")
    console.print(f"[dim]Notice: redacted code around each selected finding will be sent to {provider.name} "
                  f"({provider.model}) to draft a minimal fix. Nothing is changed without your approval.[/dim]")

    def show(p: Proposal) -> None:
        f = p.finding
        console.print(Panel(f"{f.title}\n[dim]{p.rel}:{f.line} · {f.severity.value} · {f.verification.value}[/dim]",
                            title=f"{f.id}"))
        for e in p.edits:
            console.print(f"  [bold]Why:[/bold] {e.reason or '(no reason given)'}")
        console.print(Syntax(p.diff, "diff", theme="ansi_dark"))

    approvals: dict[str, bool] = {}

    def cached_confirm(message: str) -> bool:  # approve each exact command once per session
        if message not in approvals:
            approvals[message] = _confirm(message)
        return approvals[message]

    policy = ApprovalPolicy(trusted=trust_tools or settings.tools.trusted, prompt=cached_confirm)
    suite = None if no_tools else ToolSuite(policy, settings.tools)
    verifier = Verifier(root, settings, policy, suite, run_tests)

    hooks = Hooks(
        approve=lambda p: _confirm(f"Apply this change to {p.rel}?"), show=show,
        notify=lambda kind, msg: console.print(
            f"[{'red' if kind in ('rolled-back', 'error') else 'yellow' if kind == 'skip' else 'green'}]{msg}[/]"))
    session = run_fix_session(root, provider, fixable, hooks, verifier, dry_run)

    report_path = write_report(render_fix_report(session), out_dir, FIX_REPORT_NAME)
    t = Table(show_header=False, box=None)
    for label, key in (("Applied (verified)", "applied"), ("Applied (unverified)", "applied-unverified"),
                       ("Rolled back", "rolled-back"), ("Declined", "declined"), ("Skipped", "skipped"),
                       ("Dry-run proposals", "dry-run")):
        if session.count(key):
            t.add_row(label + ":", str(session.count(key)))
    console.print(Panel(t, title="FIX RESULTS"))
    if session.count("applied") + session.count("applied-unverified") + session.count("rolled-back"):
        console.print(f"Originals backed up to: [bold]{session.backup_dir}[/bold]")
    console.print(f"Report: [bold]{report_path}[/bold]  [dim](re-run `repo-doctor analyze` to refresh findings)[/dim]")
    if session.count("rolled-back"):
        raise typer.Exit(2)


@app.command()
def report(out: OutOpt = None):
    """Display the most recently generated report."""
    from rich.markdown import Markdown
    d = out or Path("reports")
    found = [d / n for n in (*REPORT_NAMES.values(), OVERVIEW_NAME) if (d / n).exists()]
    if not found:
        raise _fail(f"No report found in {d}. Run `repo-doctor analyze` first.")
    target = max(found, key=lambda p: p.stat().st_mtime)
    console.print(Markdown(target.read_text(encoding="utf-8")))
    console.print(f"[dim]{target}[/dim]")
