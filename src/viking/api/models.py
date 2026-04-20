"""Pydantic models for menu and order entities.

Field names follow the API's expected vocabulary, but exact mapping is
finalised in Phase 1 (HAR discovery). Keep this module the single source of
truth — `api/client.py` adapts the wire format into these models.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, NonNegativeFloat


# Slot names are the Polish meal names as returned by the API,
# e.g. "Śniadanie", "II śniadanie", "Obiad", "Podwieczorek", "Kolacja".
SlotName = str


class Macros(BaseModel):
    kcal: NonNegativeFloat
    protein: NonNegativeFloat
    fat: NonNegativeFloat
    carbs: NonNegativeFloat

    def __add__(self, other: "Macros") -> "Macros":
        return Macros(
            kcal=self.kcal + other.kcal,
            protein=self.protein + other.protein,
            fat=self.fat + other.fat,
            carbs=self.carbs + other.carbs,
        )


class Dish(BaseModel):
    id: str
    name: str
    description: str = ""
    macros: Macros
    price: float | None = None
    weight_g: float | None = None
    tags: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)


class MenuSlot(BaseModel):
    """Available choices for a single meal slot on a single day."""

    slot: SlotName
    options: list[Dish]
    current_dish_id: str | None = None


class MenuDay(BaseModel):
    date: date
    slots: list[MenuSlot]

    def dish(self, dish_id: str) -> Dish | None:
        for s in self.slots:
            for d in s.options:
                if d.id == dish_id:
                    return d
        return None


class OrderSelection(BaseModel):
    """Currently chosen dish for one slot on one day."""

    date: date
    slot: SlotName
    dish_id: str


class Plan(BaseModel):
    """LLM-produced plan for a date range."""

    selections: list[OrderSelection]
    rationale: str = ""

    def by_day(self) -> dict[date, list[OrderSelection]]:
        out: dict[date, list[OrderSelection]] = {}
        for s in self.selections:
            out.setdefault(s.date, []).append(s)
        return out
