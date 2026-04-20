"""CLI entry point: `viking ...`"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from viking.ai.selector import build_caller, daily_macros, select_plan
from viking.api.client import VikingClient
from viking.api.models import MenuDay, Plan
from viking.config import Settings

app = typer.Typer(add_completion=False, help="AI menu picker for Kuchnia Vikinga.")
console = Console()


# --- helpers ---------------------------------------------------------------


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _load_menu_fixture(path: Path) -> list[MenuDay]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [MenuDay.model_validate(d) for d in raw]


def _resolve_default_prompt(settings: Settings) -> Path | None:
    """Return the first existing default-prompt path, or None.

    Lookup order:
    1. $VIKING_PROMPT_FILE (settings.prompt_file)
    2. <state_dir>/prompt.md  (e.g. ~/.viking/prompt.md)
    3. ./prompts/default.md  (repo bundled default)
    """
    candidates: list[Path] = []
    if settings.prompt_file:
        candidates.append(settings.prompt_file)
    candidates.append(settings.state_dir / "prompt.md")
    candidates.append(Path(__file__).resolve().parents[2] / "prompts" / "default.md")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _read_prompt(
    prompt: str | None, prompt_file: Path | None, settings: Settings
) -> tuple[str, str]:
    """Return (prompt_text, source_label)."""
    if prompt and prompt_file:
        raise typer.BadParameter("Use either --prompt or --prompt-file, not both.")
    if prompt_file:
        return prompt_file.read_text(encoding="utf-8"), str(prompt_file)
    if prompt:
        return prompt, "--prompt"
    default = _resolve_default_prompt(settings)
    if default is None:
        raise typer.BadParameter(
            "Provide --prompt or --prompt-file, set $VIKING_PROMPT_FILE, "
            f"or create {settings.state_dir / 'prompt.md'}."
        )
    return default.read_text(encoding="utf-8"), str(default)


def _plan_path(state_dir: Path) -> Path:
    return state_dir / "last_plan.json"


def _plans_dir() -> Path:
    """Workspace `plans/` directory next to the package source."""
    d = Path(__file__).resolve().parents[2] / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest_plan_file(plans_dir: Path) -> Path | None:
    files = sorted(plans_dir.glob("*.json"))
    return files[-1] if files else None


# --- commands --------------------------------------------------------------


@app.command()
def fetch(
    date_from: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    date_to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    out: Path | None = typer.Option(None, "--out", help="Save menu JSON to file."),
) -> None:
    """Fetch the menu for a date range and print/save it."""
    s = Settings.load()
    with VikingClient(s.base_url, s.email, s.password) as client:
        client.login()
        menu = client.list_menu(_parse_date(date_from), _parse_date(date_to))
    payload = [m.model_dump(mode="json") for m in menu]
    if out:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Saved {len(menu)} day(s) to {out}[/green]")
    else:
        console.print_json(data=payload)


@app.command()
def plan(
    date_from: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    date_to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    prompt: str | None = typer.Option(None, "--prompt", "-p"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", "-f"),
    menu_file: Path | None = typer.Option(
        None,
        "--menu-file",
        help="Read menu from JSON instead of fetching (for offline/testing).",
    ),
) -> None:
    """Build a meal plan with the LLM and print it; saves last plan to state dir."""
    settings = Settings.load()
    user_prompt, prompt_source = _read_prompt(prompt, prompt_file, settings)
    console.print(f"[dim]Prompt source: {prompt_source}[/dim]")

    if menu_file:
        menu = _load_menu_fixture(menu_file)
    else:
        with VikingClient(settings.base_url, settings.email, settings.password) as client:
            client.login()
            menu = client.list_menu(_parse_date(date_from), _parse_date(date_to))

    if not settings.openai_api_key and not settings.github_token:
        raise typer.BadParameter(
            "No LLM credentials. Set GITHUB_TOKEN (default) or OPENAI_API_KEY."
        )
    llm = build_caller(
        settings.llm_provider, settings.llm_credential(), settings.llm_model
    )
    result = select_plan(menu, user_prompt, llm)

    _print_plan(menu, result)

    payload = {
        "date_from": date_from,
        "date_to": date_to,
        "prompt": user_prompt,
        "plan": result.model_dump(mode="json"),
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)

    plans_dir = _plans_dir()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_file = plans_dir / f"{ts}_{date_from}_{date_to}.json"
    plan_file.write_text(body, encoding="utf-8")
    # also keep last_plan.json in state dir for backwards-compat / quick apply
    _plan_path(settings.state_dir).write_text(body, encoding="utf-8")
    console.print(f"\n[dim]Saved plan to {plan_file}[/dim]")


@app.command()
def apply(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show diff without applying."),
    plan_file: Path | None = typer.Option(
        None,
        "--plan",
        "-P",
        help="Plan JSON to apply. Defaults to the newest file in plans/.",
    ),
) -> None:
    """Apply a saved `plan` to the panel."""
    settings = Settings.load()
    if plan_file is None:
        plan_file = _latest_plan_file(_plans_dir())
        if plan_file is None:
            # legacy fallback
            legacy = _plan_path(settings.state_dir)
            if legacy.exists():
                plan_file = legacy
        if plan_file is None:
            raise typer.BadParameter(
                "No saved plans found. Run `viking plan` first "
                "or pass --plan <file>."
            )
    console.print(f"[dim]Plan source: {plan_file}[/dim]")
    saved = json.loads(plan_file.read_text(encoding="utf-8"))
    plan_obj = Plan.model_validate(saved["plan"])

    console.print(f"[bold]Plan to apply ({len(plan_obj.selections)} selections):[/bold]")
    for sel in plan_obj.selections:
        console.print(f"  {sel.date}  {sel.slot:>20s}  -> {sel.dish_id}")

    if dry_run:
        console.print("[yellow]Dry run; nothing applied.[/yellow]")
        return
    if not yes and not typer.confirm("Apply these selections to the panel?", default=False):
        console.print("[red]Aborted.[/red]")
        raise typer.Exit(code=1)

    with VikingClient(settings.base_url, settings.email, settings.password) as client:
        client.login()
        # Need to (re)load menu for the plan's date range so the client knows
        # the (deliveryId, deliveryMealId) for each (date, slot).
        dates = [s.date for s in plan_obj.selections]
        client.list_menu(min(dates), max(dates))
        client.apply(plan_obj.selections)
    console.print("[green]Applied.[/green]")


# --- rendering -------------------------------------------------------------


def _print_plan(menu: list[MenuDay], result: Plan) -> None:
    by_date = {d.date: d for d in menu}
    table = Table(title="Selected dishes", show_lines=False)
    table.add_column("Date")
    table.add_column("Slot")
    table.add_column("Dish")
    table.add_column("kcal", justify="right")
    table.add_column("P", justify="right")
    table.add_column("F", justify="right")
    table.add_column("C", justify="right")
    for sel in sorted(result.selections, key=lambda s: (s.date, s.slot)):
        dish = by_date[sel.date].dish(sel.dish_id)
        if dish is None:
            continue
        m = dish.macros
        table.add_row(
            sel.date.isoformat(),
            sel.slot,
            dish.name,
            f"{m.kcal:.0f}",
            f"{m.protein:.0f}",
            f"{m.fat:.0f}",
            f"{m.carbs:.0f}",
        )
    console.print(table)

    totals = daily_macros(menu, result)
    summary = Table(title="Daily totals")
    summary.add_column("Date")
    summary.add_column("kcal", justify="right")
    summary.add_column("P", justify="right")
    summary.add_column("F", justify="right")
    summary.add_column("C", justify="right")
    for d, t in sorted(totals.items()):
        summary.add_row(
            d,
            f"{t['kcal']:.0f}",
            f"{t['protein']:.0f}",
            f"{t['fat']:.0f}",
            f"{t['carbs']:.0f}",
        )
    console.print(summary)
    if result.rationale:
        console.print(f"\n[bold]Rationale:[/bold] {result.rationale}")


if __name__ == "__main__":
    app()
