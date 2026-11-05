from __future__ import annotations

import json
import shutil
import subprocess
import sys

import click

import sherpa
from sherpa.interviewer import InterviewSession, State
from sherpa.scout import LINKEDIN_NOTE_LIMIT
from sherpa.telemetry import burnout_status
from sherpa.tracker import ValidationGap


class _SherpaGroup(click.Group):
    """Turns LLM backend failures into a clean CLI error instead of a traceback."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except sherpa.ProviderError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_SherpaGroup)
def cli() -> None:
    """Interview Sherpa - local-first SDE2 prep assistant."""


@cli.command()
def init() -> None:
    """Bootstrap the ~/.sherpa/ workspace."""
    result = sherpa.init_workspace()
    for path, status in result.items():
        click.echo(f"  {status:8s}  {path}")
    click.echo("Workspace ready.")


_FIELD_LABELS = {"company": "Company", "stage": "Stage"}


@cli.command()
@click.argument("text")
def track(text: str) -> None:
    """Track a job application from natural language TEXT."""
    result = sherpa.track(text)
    if isinstance(result, ValidationGap):
        click.secho(f"Need a bit more: {', '.join(_FIELD_LABELS[f] for f in result.missing)}", fg="yellow")
        answers = {
            field: click.prompt(_FIELD_LABELS[field], type=click.Choice(sherpa.application_stages(), case_sensitive=False) if field == "stage" else str)
            for field in result.missing
        }
        result = sherpa.complete_track(result.partial, answers)
    click.secho("Tracked:", fg="green")
    click.echo(json.dumps(result, indent=2))


_EDITOR_MARKER = "# Paste the job post above. Lines starting with '#' are ignored."


# Tried in order; the first one installed is used.
_CLIPBOARD_COMMANDS = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip"])


def _copy_to_clipboard(text: str) -> bool:
    for command in _CLIPBOARD_COMMANDS:
        if shutil.which(command[0]):
            return subprocess.run(command, input=text, text=True, check=False).returncode == 0
    return False


@cli.command()
@click.option("--file", "job_file", type=click.File("r"), help="Read the job post from a file.")
@click.option(
    "--copy", "copy_part", type=click.Choice(["note", "email", "none"]), default="note", show_default=True,
    help="Which part to put on the clipboard.",
)
@click.option("--last", is_flag=True, help="Show the most recent saved draft instead of writing a new one.")
def outreach(job_file, copy_part: str, last: bool) -> None:
    """Draft a LinkedIn note and cold email for a job post.

    Reads the job post from --file, from stdin (e.g. `pbpaste | sherpa outreach`),
    or opens your editor so you can paste it. The LinkedIn note is copied to the
    clipboard; use `sherpa outreach --last --copy email` to copy the email afterwards.
    """
    if last:
        draft = sherpa.latest_outreach()
        if draft is None:
            raise click.ClickException("No saved outreach drafts yet.")
        _show_draft(draft, copy_part)
        return

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
    _show_draft(draft, copy_part)


def _show_draft(draft: sherpa.OutreachDraft, copy_part: str) -> None:
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

    if copy_part == "none":
        return
    text = draft.linkedin_note if copy_part == "note" else f"Subject: {draft.email_subject}\n\n{draft.email_body}"
    if _copy_to_clipboard(text):
        other = "note" if copy_part == "email" else "email"
        click.secho(f"Copied the {copy_part} to your clipboard. For the {other}: sherpa outreach --last --copy {other}", fg="green")
    else:
        click.secho("Could not find a clipboard tool (pbcopy, wl-copy, xclip); copy it from above.", fg="yellow")


@cli.command()
@click.argument("topic", required=False)
@click.option("--question", "-q", help="Start on a question-bank question by name (see --list).")
@click.option(
    "--kind",
    type=click.Choice([k.value for k in sherpa.Kind]),
    help="Interview flow: hld (system design), lld (class design), dsa (coding). "
    "Defaults to the question's kind, else hld.",
)
@click.option("--list", "list_bank", is_flag=True, help="List the question bank and exit.")
def interview(topic: str | None, question: str | None, kind: str | None, list_bank: bool) -> None:
    """Start or resume a mock technical interview.

    If TOPIC or --question is given, starts a new session.
    If neither is given, resumes the most recent non-FEEDBACK session from today.
    While the burnout guard is on, new sessions are refused and a resumed
    session continues in advisory mode.
    """
    if list_bank:
        questions = sherpa.list_questions()
        if not questions:
            raise click.ClickException("The question bank is empty. Run `sherpa init` to seed it.")
        for q in questions:
            click.echo(f"  {q.name:28s} {q.kind.value:4s} {q.title}")
        return

    session: InterviewSession
    if topic or question:
        try:
            session = InterviewSession.start(topic, question=question, kind=sherpa.Kind(kind) if kind else None)
        except (RuntimeError, sherpa.QuestionNotFoundError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.secho(f"New session: {session.path.name}", fg="green")
    else:
        session = InterviewSession.latest_open()
        if session is None:
            raise click.ClickException("No in-progress session found today. Pass a TOPIC or --question to start one.")
        click.secho(f"Resuming: {session.path.name}", fg="cyan")

    click.echo(f"Topic: {session.topic} ({session.kind.value})")
    click.echo(f"State: {session.state.value}\n")
    _repl(session)


_FENCE = "```"


def _read_message() -> str:
    """Read one message; a line that opens a ``` fence keeps reading until the fence closes."""
    lines = [click.prompt("You", prompt_suffix="> ")]
    while lines[0].count(_FENCE) % 2 == 1:
        line = input()
        lines.append(line)
        if line.count(_FENCE) % 2 == 1:
            break
    return "\n".join(lines)


def _repl(session: InterviewSession) -> None:
    click.echo("Type your response below. Paste code between ``` fences. Enter 'quit' or Ctrl+D to exit.\n")
    while True:
        try:
            user_input = _read_message()
        except (click.exceptions.Abort, EOFError):
            click.echo("\nSession paused. Resume with: sherpa interview")
            break
        if user_input.strip().lower() in ("quit", "exit"):
            click.echo("Session paused. Resume with: sherpa interview")
            break

        prev_state = session.state
        response = session.turn(user_input)

        if session.last_review is not None:
            click.secho(f"\n{session.last_review.as_markdown()}", fg="magenta")
        click.echo(f"\nInterviewer:\n{response}\n")
        if session.last_reply_advisory:
            click.secho("(Advisory mode - burnout guard is on. Time for a break.)\n", fg="yellow")

        if session.state != prev_state:
            click.secho(f"--- State advanced: {prev_state.value} → {session.state.value} ---\n", fg="cyan")

        if session.state == State.FEEDBACK:
            click.secho("Interview complete. See feedback above.", fg="green")
            break


@cli.command()
@click.option("--file", "code_file", type=click.File("r"), help="Read the code from a file instead of stdin.")
@click.option("--problem", default="", help="The problem the code solves, for context.")
def review(code_file, problem: str) -> None:
    """Score code for correctness, thread safety, and complexity.

    Reads code from --file or stdin (e.g. `pbpaste | sherpa review`).
    """
    code = code_file.read() if code_file is not None else sys.stdin.read()
    if not code.strip():
        raise click.ClickException("No code given. Pass --file or pipe code in.")
    try:
        result = sherpa.evaluate_code(code, problem=problem)
    except sherpa.CodeReviewError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result.as_markdown())


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

    today = sherpa.daily_activity()
    click.echo("\nToday (only interview minutes count toward the limit):")
    click.echo(f"  Applications tracked : {today.applications}")
    click.echo(f"  Outreach drafts      : {today.outreach_drafts}")
    click.echo(f"\nApplications tracked (all time): {len(sherpa.load_applications())}")
