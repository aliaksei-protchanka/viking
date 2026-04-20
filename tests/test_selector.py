import json
from pathlib import Path

import pytest

from viking.ai.selector import (
    build_menu_payload,
    daily_macros,
    select_plan,
)
from viking.api.models import MenuDay, Plan


FIXTURE = Path(__file__).parent / "fixtures" / "menu_sample.json"


@pytest.fixture
def menu() -> list[MenuDay]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [MenuDay.model_validate(d) for d in raw]


def _fake_llm(plan_dict: dict):
    def _call(messages, response_format):
        # sanity: schema and slot enum are present
        assert response_format["type"] == "json_schema"
        return json.dumps(plan_dict)
    return _call


def test_build_menu_payload_compact(menu):
    payload = build_menu_payload(menu)
    assert len(payload) == 2
    first = payload[0]
    assert first["date"] == "2026-04-21"
    # compact macro keys
    sample = first["slots"][0]["options"][0]
    assert {"id", "name", "kcal", "p", "f", "c"} <= sample.keys()


def test_select_plan_valid(menu):
    plan_dict = {
        "rationale": "ok",
        "selections": [
            {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "b2"},
            {"date": "2026-04-21", "slot": "obiad", "dish_id": "o1"},
            {"date": "2026-04-21", "slot": "kolacja", "dish_id": "k3"},
            {"date": "2026-04-22", "slot": "sniadanie", "dish_id": "b4"},
            {"date": "2026-04-22", "slot": "obiad", "dish_id": "o4"},
            {"date": "2026-04-22", "slot": "kolacja", "dish_id": "k5"},
        ],
    }
    plan = select_plan(menu, "high protein, no fish", _fake_llm(plan_dict))
    assert isinstance(plan, Plan)
    assert len(plan.selections) == 6


def test_select_plan_rejects_unknown_dish(menu):
    bad = {
        "rationale": "x",
        "selections": [
            {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "ZZZ"},
            {"date": "2026-04-21", "slot": "obiad", "dish_id": "o1"},
            {"date": "2026-04-21", "slot": "kolacja", "dish_id": "k3"},
            {"date": "2026-04-22", "slot": "sniadanie", "dish_id": "b4"},
            {"date": "2026-04-22", "slot": "obiad", "dish_id": "o4"},
            {"date": "2026-04-22", "slot": "kolacja", "dish_id": "k5"},
        ],
    }
    with pytest.raises(ValueError, match="not among options"):
        select_plan(menu, "p", _fake_llm(bad), max_retries=0)


def test_select_plan_rejects_missing_slot(menu):
    incomplete = {
        "rationale": "x",
        "selections": [
            {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "b1"},
        ],
    }
    with pytest.raises(ValueError, match="missing selection"):
        select_plan(menu, "p", _fake_llm(incomplete), max_retries=0)


def test_select_plan_retries_then_succeeds(menu):
    bad = {
        "rationale": "x",
        "selections": [
            {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "ZZZ"},
        ],
    }
    good = {
        "rationale": "ok",
        "selections": [
            {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "b1"},
            {"date": "2026-04-21", "slot": "obiad", "dish_id": "o1"},
            {"date": "2026-04-21", "slot": "kolacja", "dish_id": "k3"},
            {"date": "2026-04-22", "slot": "sniadanie", "dish_id": "b4"},
            {"date": "2026-04-22", "slot": "obiad", "dish_id": "o4"},
            {"date": "2026-04-22", "slot": "kolacja", "dish_id": "k5"},
        ],
    }
    responses = [json.dumps(bad), json.dumps(good)]
    calls = {"n": 0}

    def llm(messages, response_format):
        r = responses[calls["n"]]
        calls["n"] += 1
        return r

    plan = select_plan(menu, "p", llm, max_retries=1)
    assert calls["n"] == 2
    assert len(plan.selections) == 6


def test_daily_macros(menu):
    plan = Plan.model_validate(
        {
            "rationale": "",
            "selections": [
                {"date": "2026-04-21", "slot": "sniadanie", "dish_id": "b2"},
                {"date": "2026-04-21", "slot": "obiad", "dish_id": "o1"},
                {"date": "2026-04-21", "slot": "kolacja", "dish_id": "k3"},
            ],
        }
    )
    totals = daily_macros(menu, plan)
    assert totals["2026-04-21"]["kcal"] == pytest.approx(480 + 720 + 380)
    assert totals["2026-04-21"]["protein"] == pytest.approx(32 + 55 + 38)
