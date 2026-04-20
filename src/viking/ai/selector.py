"""Build LLM prompts and parse structured plan responses."""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from pydantic import ValidationError

from viking.ai.schema import plan_response_schema
from viking.api.models import MenuDay, Plan, OrderSelection

LLMCaller = Callable[[list[dict[str, str]], dict[str, Any]], str]
"""Signature: (messages, response_format) -> raw JSON string from the model."""


SYSTEM_PROMPT = (
    "You are a precise nutrition-aware meal planner. The user receives daily "
    "catering and must choose exactly one dish per available meal slot per "
    "day. Optimise the choices to satisfy the user's freeform criteria "
    "(macros, calories, allergens, preferences, variety). You MUST only use "
    "dish_id values that appear in the provided menu. If the user's targets "
    "cannot be met exactly, get as close as possible and explain trade-offs "
    "in 'rationale'. Respond ONLY with the structured JSON."
)


def build_menu_payload(menu: Iterable[MenuDay]) -> list[dict[str, Any]]:
    """Compact JSON representation of menu sent to the LLM (token-efficient)."""
    out: list[dict[str, Any]] = []
    for day in menu:
        out.append(
            {
                "date": day.date.isoformat(),
                "slots": [
                    {
                        "slot": s.slot,
                        "options": [
                            {
                                "id": d.id,
                                "name": d.name,
                                "kcal": d.macros.kcal,
                                "p": d.macros.protein,
                                "f": d.macros.fat,
                                "c": d.macros.carbs,
                                **({"weight_g": d.weight_g} if d.weight_g else {}),
                                **({"tags": d.tags} if d.tags else {}),
                                **({"allergens": d.allergens} if d.allergens else {}),
                            }
                            for d in s.options
                        ],
                    }
                    for s in day.slots
                ],
            }
        )
    return out


def _collect_slot_names(menu: Iterable[MenuDay]) -> list[str]:
    seen: list[str] = []
    for day in menu:
        for s in day.slots:
            if s.slot not in seen:
                seen.append(s.slot)
    return seen


def _validate_against_menu(plan: Plan, menu: list[MenuDay]) -> list[str]:
    """Return a list of human-readable validation errors (empty if valid)."""
    errors: list[str] = []
    by_date = {d.date: d for d in menu}
    expected_slots = {(d.date, s.slot) for d in menu for s in d.slots}
    chosen_slots: set[tuple] = set()

    for sel in plan.selections:
        day = by_date.get(sel.date)
        if day is None:
            errors.append(f"date {sel.date} not in menu")
            continue
        slot = next((s for s in day.slots if s.slot == sel.slot), None)
        if slot is None:
            errors.append(f"slot {sel.slot} not available on {sel.date}")
            continue
        if not any(d.id == sel.dish_id for d in slot.options):
            errors.append(
                f"dish_id {sel.dish_id!r} not among options for {sel.date} {sel.slot}"
            )
            continue
        if (sel.date, sel.slot) in chosen_slots:
            errors.append(f"duplicate selection for {sel.date} {sel.slot}")
        chosen_slots.add((sel.date, sel.slot))

    missing = expected_slots - chosen_slots
    for d, s in sorted(missing, key=lambda x: (x[0], x[1])):
        errors.append(f"missing selection for {d} {s}")
    return errors


def select_plan(
    menu: list[MenuDay],
    user_prompt: str,
    llm: LLMCaller,
    *,
    max_retries: int = 1,
) -> Plan:
    """Ask the LLM to pick dishes; validate and retry once on errors."""
    if not menu:
        raise ValueError("menu is empty")

    payload = build_menu_payload(menu)
    slot_names = _collect_slot_names(menu)
    response_format = {"type": "json_schema", "json_schema": plan_response_schema(slot_names)}

    user_msg = (
        "User criteria (freeform):\n"
        f"{user_prompt.strip()}\n\n"
        "Menu (one entry per day; pick exactly one dish per slot):\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        if last_error is not None:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response had problems:\n"
                        f"{last_error}\n"
                        "Reply again with a corrected JSON plan."
                    ),
                }
            )
        raw = llm(messages, response_format)
        try:
            data = json.loads(raw)
            plan = Plan.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = f"Response was not valid JSON matching the schema: {e}"
            if attempt == max_retries:
                raise ValueError(last_error) from e
            continue

        errors = _validate_against_menu(plan, menu)
        if not errors:
            return plan
        last_error = "\n".join(f"- {e}" for e in errors)
        if attempt == max_retries:
            raise ValueError(f"LLM produced invalid plan after retries:\n{last_error}")
    raise RuntimeError("unreachable")  # pragma: no cover


def openai_caller(api_key: str, model: str) -> LLMCaller:
    """Build an LLMCaller backed by the OpenAI Chat Completions API."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    return _chat_completions_caller(client, model)


def github_models_caller(github_token: str, model: str) -> LLMCaller:
    """Build an LLMCaller backed by GitHub Models (OpenAI-compatible).

    Free for personal use. Requires a PAT with `models:read` scope.
    Model identifiers look like `openai/gpt-4o-mini`, `openai/gpt-4o`, etc.
    See https://github.com/marketplace?type=models for the catalog.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=github_token,
        base_url="https://models.github.ai/inference",
    )
    return _chat_completions_caller(client, model)


def _chat_completions_caller(client, model: str) -> LLMCaller:
    def _call(messages: list[dict[str, str]], response_format: dict[str, Any]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format=response_format,  # type: ignore[arg-type]
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""

    return _call


def anthropic_caller(api_key: str, model: str) -> LLMCaller:
    """Build an LLMCaller backed by Anthropic (Claude).

    Uses the tool-use API to enforce a structured JSON output that matches the
    same schema we send to OpenAI. The tool's `input` is what we return as
    the JSON string.
    """
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    def _call(messages: list[dict[str, str]], response_format: dict[str, Any]) -> str:
        # response_format is OpenAI shape: {"type":"json_schema","json_schema":{...}}
        schema = response_format["json_schema"]["schema"]
        tool_name = response_format["json_schema"]["name"]
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msgs = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0.2,
            system=system,
            messages=[{"role": m["role"], "content": m["content"]} for m in user_msgs],
            tools=[
                {
                    "name": tool_name,
                    "description": "Return the meal plan as structured data.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return json.dumps(block.input, ensure_ascii=False)
        # fallback: text content
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

    return _call


def build_caller(provider: str, credential: str, model: str) -> LLMCaller:
    if provider == "openai":
        return openai_caller(credential, model)
    if provider == "github":
        return github_models_caller(credential, model)
    if provider == "anthropic":
        return anthropic_caller(credential, model)
    raise ValueError(f"Unknown LLM provider {provider!r}")


def daily_macros(menu: list[MenuDay], plan: Plan) -> dict[str, dict[str, float]]:
    """Aggregate macros per day for a validated plan."""
    out: dict[str, dict[str, float]] = {}
    by_date = {d.date: d for d in menu}
    grouped: dict = {}
    for sel in plan.selections:
        grouped.setdefault(sel.date, []).append(sel)
    for d, sels in grouped.items():
        day = by_date[d]
        totals = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
        for sel in sels:
            dish = day.dish(sel.dish_id)
            if dish is None:
                continue
            totals["kcal"] += dish.macros.kcal
            totals["protein"] += dish.macros.protein
            totals["fat"] += dish.macros.fat
            totals["carbs"] += dish.macros.carbs
        out[d.isoformat()] = totals
    return out


__all__ = [
    "select_plan",
    "build_menu_payload",
    "openai_caller",
    "github_models_caller",
    "anthropic_caller",
    "build_caller",
    "daily_macros",
    "LLMCaller",
]
