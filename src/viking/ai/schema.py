"""JSON Schema for the LLM's structured response."""
from __future__ import annotations

from typing import Any


def plan_response_schema(slot_names: list[str]) -> dict[str, Any]:
    """Return a JSON Schema describing a meal plan.

    Used with OpenAI `response_format={"type": "json_schema", ...}` so the
    model output is guaranteed to deserialize into `viking.api.models.Plan`.
    """
    return {
        "name": "meal_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["selections", "rationale"],
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": (
                        "Short explanation (2-5 sentences) of how the chosen "
                        "dishes satisfy the user's criteria."
                    ),
                },
                "selections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["date", "slot", "dish_id"],
                        "properties": {
                            "date": {
                                "type": "string",
                                "description": "ISO 8601 date, e.g. 2026-04-21",
                            },
                            "slot": {"type": "string", "enum": slot_names},
                            "dish_id": {
                                "type": "string",
                                "description": "Must equal one of the dish ids from the menu input.",
                            },
                        },
                    },
                },
            },
        },
    }
