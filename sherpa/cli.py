from __future__ import annotations

import json
import sys
from pathlib import Path

import click

import sherpa
from sherpa.interviewer import InterviewSession, State
from sherpa.scout import LINKEDIN_NOTE_LIMIT
from sherpa.telemetry import burnout_status, is_blocked
from sherpa.tracker import ValidationGap
from sherpa.workspace import workspace_paths


@click.group()
def cli() -> None:
    """Interview Sherpa - local-first SDE2 prep assistant."""


@cli.command()
def init() -> None:
    """Bootstrap the ~/.sherpa/ workspace."""
    result = sherpa.init_workspace()
    for path, status in result.items():
        click.echo(f"  {status:8s}  {path}")
    click.echo("Workspace ready.")


@cli.command()
@click.argument("text")
def track(text: str) -> None:
    """Track a job application from natural language TEXT."""
    result = sherpa.track(text)
    if isinstance(result, ValidationGap):
        click.secho(f"Missing required fields: {', '.join(result.missing)}", fg="yellow")
        click.echo("Try again with more detail, e.g. 'Applied to Stripe for SDE2'.")
        sys.exit(1)
    click.secho("Tracked:", fg="green")
    click.echo(json.dumps(result, indent=2))


_EDITOR_MARKER = "# Paste the job post above. Lines starting with '#' are ignored."


@cli.command()
@click.option("--file", "job_file", type=click.File("r"), help="Read the job post from a file.")
def outreach(job_file) -> None:
    """Draft a LinkedIn note and cold email for a job post.

    Reads the job post from --file, from stdin (e.g. `pbpaste | sherpa outreach`),
    or opens your editor so you can paste it.
    """
    if job_file is not None:
        job_post = job_file.read()
    elif not sys.stdin.isatty():
        job_post = sys.stdin.read()
    else:
        edited = click.edit(f"\n\n{_EDITOR_MARKER}\n") or ""
        job_post = "\n".join(line for line in edited.splitlines() if not line.startswith("#"))

    if not job_post.strip():
        raise click.ClickException("No job post given. Paste one, pipe it in, or pass --file.")

    try:
        draft = sherpa.draft_outreach(job_post)
    except sherpa.OutreachError as exc:
        raise click.ClickException(str(exc)) from exc

    title = f"{draft.company} - {draft.role}" if draft.role else draft.company
    click.secho(title, bold=True)
    match = "Strong match" if draft.aligned else "Weak match"
    source = "full resume used" if draft.used_full_resume else "resume index only"
    click.secho(f"{match}, {source}\n", fg="green" if draft.aligned else "yellow")

    click.secho(f"LinkedIn note ({len(draft.linkedin_note)}/{LINKEDIN_NOTE_LIMIT} chars)", fg="cyan")
    click.echo(f"{draft.linkedin_note}\n")
    click.secho("Email", fg="cyan")
    click.echo(f"Subject: {draft.email_subject}\n")
    click.echo(f"{draft.email_body}\n")
    click.echo(f"Saved to {draft.path}")


@cli.command()
@click.argument("topic", required=False)
def interview(topic: str | None) -> None:
    """Start or resume a mock technical interview.

    If TOPIC is given, starts a new session.
    If omitted, resumes the most recent non-FEEDBACK session from today.
    """
    if is_blocked():
        status = burnout_status()
        unblocks = status["unblocks_at"] or "later"
        raise click.ClickException(
            f"Burnout guard active: {status['daily_minutes']:.0f}/{status['threshold']} active minutes today. "
            f"Resume after {unblocks}."
        )

    session: InterviewSession
    if topic:
        try:
            session = InterviewSession.start(topic)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        click.secho(f"New session: {session.path.name}", fg="green")
    else:
        session = _resume_latest()
        if session is None:
            raise click.ClickException("No in-progress session found today. Pass a TOPIC to start one.")
        click.secho(f"Resuming: {session.path.name}", fg="cyan")

    click.echo(f"State: {session.state.value}\n")
    _repl(session)


def _resume_latest() -> InterviewSession | None:
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    interviews_dir = workspace_paths()["interviews"]
    candidates = sorted(interviews_dir.glob(f"session_{date_str}_*.md"), reverse=True)
    for path in candidates:
        try:
            s = InterviewSession.resume(path)
            if s.state != State.FEEDBACK:
                return s
        except Exception:
            continue
    return None


def _repl(session: InterviewSession) -> None:
    click.echo("Type your response below. Enter 'quit' or Ctrl+D to exit.\n")
    while True:
        try:
            user_input = click.prompt("You", prompt_suffix="> ")
        except (click.exceptions.Abort, EOFError):
            click.echo("\nSession paused. Resume with: sherpa interview")
            break
        if user_input.strip().lower() in ("quit", "exit"):
            click.echo("Session paused. Resume with: sherpa interview")
            break

        prev_state = session.state
        response = session.turn(user_input)

        click.echo(f"\nInterviewer:\n{response}\n")

        if session.state != prev_state:
            click.secho(f"--- State advanced: {prev_state.value} → {session.state.value} ---\n", fg="cyan")

        if session.state == State.FEEDBACK:
            click.secho("Interview complete. See feedback above.", fg="green")
            break


@cli.command()
def stats() -> None:
    """Show daily prep stats and burnout status."""
    status = burnout_status()
    minutes = status["daily_minutes"]
    threshold = status["threshold"]
    blocked = status["blocked"]

    click.echo(f"Daily active minutes : {minutes:.1f} / {threshold}")
    bar_filled = int(min(minutes / max(threshold, 1), 1.0) * 20)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    color = "red" if blocked else ("yellow" if minutes / max(threshold, 1) > 0.75 else "green")
    click.secho(f"  [{bar}]", fg=color)

    if blocked:
        click.secho(f"BLOCKED - cooldown active. Unblocks at: {status['unblocks_at']}", fg="red")
    else:
        remaining = threshold - minutes
        click.secho(f"OK - {remaining:.1f} minutes remaining today.", fg="green")

    apps_path = workspace_paths()["applications"]
    app_count = 0
    if apps_path.exists():
        app_count = sum(1 for line in apps_path.read_text().splitlines() if line.strip())
    click.echo(f"Applications tracked : {app_count}")
