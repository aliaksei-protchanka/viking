"""HTTP client for panel.kuchniavikinga.pl.

API map (discovered in Phase 1):
- POST /api/auth/login                           form: username, password
- GET  /api/company/customer/order/active-ids    -> [orderId]
- GET  /api/company/customer/order/{orderId}     -> {deliveries: [...]}
- GET  /api/company/general/menus/delivery/{deliveryId}/new
       -> {deliveryMenuMeal: [{deliveryMealId, mealName, dietCaloriesMealId,
                               nutrition, allergens, switchable, ...}]}
- GET  /api/company/customer/order/{orderId}/deliveries/{deliveryId}/
       delivery-meals/{deliveryMealId}/switch
       -> {mealChangeOptions: [{menuMealDetails: {dietCaloriesMealId,
           menuMealName, nutrition, allergens, mealName}, canBeChanged}]}
- PUT  same /switch?amount=1&dietCaloriesMealId=<id>
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from viking.api.models import Dish, Macros, MenuDay, MenuSlot, OrderSelection, SlotName


COMPANY_ID = "kuchniavikinga"


class AuthError(RuntimeError):
    pass


class APIError(RuntimeError):
    pass


@dataclass(frozen=True)
class _SlotRef:
    """Identifiers needed to PUT a switch for a (date, slot)."""

    order_id: int
    delivery_id: int
    delivery_meal_id: int
    meal_priority: int


class VikingClient:
    def __init__(
        self,
        base_url: str,
        email: str | None = None,
        password: str | None = None,
        *,
        company_id: str = COMPANY_ID,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._company_id = company_id
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
                "company-id": company_id,
                "x-launcher-type": "BROWSER_PANEL",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self._logged_in = False
        # (date, slot) -> _SlotRef for apply()
        self._slot_refs: dict[tuple[date, str], _SlotRef] = {}

    # --- lifecycle -----------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VikingClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low-level -----------------------------------------------------
    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        r = self._http.request(method, path, **kw)
        if r.status_code in (401, 403):
            raise AuthError(f"{r.status_code} on {method} {path} — login expired or invalid")
        if r.status_code >= 400:
            raise APIError(f"{r.status_code} on {method} {path}: {r.text[:300]}")
        return r

    # --- auth ----------------------------------------------------------
    def login(self) -> None:
        if not self._email or not self._password:
            raise AuthError("VIKING_EMAIL / VIKING_PASSWORD not set in environment")
        r = self._http.post(
            "/api/auth/login",
            data={"username": self._email, "password": self._password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise AuthError(f"login failed: {r.status_code} {r.text[:200]}")
        # sanity-check session cookie works
        p = self._http.get("/api/profile")
        if p.status_code != 200:
            raise AuthError(
                f"login appeared to succeed but /api/profile returned {p.status_code}"
            )
        self._logged_in = True

    # --- order discovery ----------------------------------------------
    def _active_order_ids(self) -> list[int]:
        r = self._request("GET", "/api/company/customer/order/active-ids")
        ids = r.json()
        if not isinstance(ids, list) or not ids:
            raise APIError("no active orders for this account")
        return [int(i) for i in ids]

    def _get_order(self, order_id: int) -> dict[str, Any]:
        r = self._request("GET", f"/api/company/customer/order/{order_id}")
        return r.json()

    def _get_delivery_menu(self, delivery_id: int) -> dict[str, Any]:
        r = self._request(
            "GET",
            f"/api/company/general/menus/delivery/{delivery_id}/new",
        )
        return r.json()

    def _get_switch_options(
        self, order_id: int, delivery_id: int, delivery_meal_id: int
    ) -> dict[str, Any]:
        r = self._request(
            "GET",
            f"/api/company/customer/order/{order_id}"
            f"/deliveries/{delivery_id}"
            f"/delivery-meals/{delivery_meal_id}/switch",
        )
        return r.json()

    def _put_switch(
        self,
        order_id: int,
        delivery_id: int,
        delivery_meal_id: int,
        diet_calories_meal_id: int,
        amount: int = 1,
    ) -> None:
        self._request(
            "PUT",
            f"/api/company/customer/order/{order_id}"
            f"/deliveries/{delivery_id}"
            f"/delivery-meals/{delivery_meal_id}/switch",
            params={"amount": amount, "dietCaloriesMealId": diet_calories_meal_id},
        )

    # --- public API ---------------------------------------------------
    def list_menu(self, date_from: date, date_to: date) -> list[MenuDay]:
        """Return menu options per day in [date_from, date_to] inclusive.

        For each delivery in range, fetch the current meals and the
        per-slot switch options. Slots that are not switchable still appear,
        but with a single option (the current dish).
        """
        if not self._logged_in:
            self.login()

        order_ids = self._active_order_ids()
        order_id = order_ids[0]  # use first active order
        order = self._get_order(order_id)

        days: list[MenuDay] = []
        skipped: list[date] = []
        for d in order.get("deliveries", []):
            if d.get("deleted"):
                continue
            d_date = date.fromisoformat(d["date"])
            if d_date < date_from or d_date > date_to:
                continue
            delivery_id = int(d["deliveryId"])
            current = self._get_delivery_menu(delivery_id)
            slots = self._build_slots(order_id, delivery_id, d_date, current)
            if not _is_day_published(slots):
                skipped.append(d_date)
                continue
            days.append(MenuDay(date=d_date, slots=slots))
        days.sort(key=lambda m: m.date)
        if skipped:
            import sys
            sys.stderr.write(
                f"[viking] skipping {len(skipped)} day(s) without published menu: "
                f"{', '.join(d.isoformat() for d in sorted(skipped))}\n"
            )
        return days

    def _build_slots(
        self,
        order_id: int,
        delivery_id: int,
        d_date: date,
        delivery_menu: dict[str, Any],
    ) -> list[MenuSlot]:
        out: list[MenuSlot] = []
        meals = delivery_menu.get("deliveryMenuMeal", [])
        meals.sort(key=lambda m: m.get("mealPriority", 0))
        for meal in meals:
            slot_name = meal["mealName"]
            delivery_meal_id = int(meal["deliveryMealId"])
            current_id = str(meal["dietCaloriesMealId"])

            # remember how to apply changes for this slot
            self._slot_refs[(d_date, slot_name)] = _SlotRef(
                order_id=order_id,
                delivery_id=delivery_id,
                delivery_meal_id=delivery_meal_id,
                meal_priority=int(meal.get("mealPriority", 0)),
            )

            if meal.get("switchable", True):
                opts = self._get_switch_options(order_id, delivery_id, delivery_meal_id)
                options = [
                    _option_to_dish(o) for o in opts.get("mealChangeOptions", [])
                    if o.get("canBeChanged", True)
                ]
                # ensure current dish is present
                if current_id not in {d.id for d in options}:
                    options.insert(0, _meal_to_dish(meal))
            else:
                options = [_meal_to_dish(meal)]

            out.append(
                MenuSlot(slot=slot_name, options=options, current_dish_id=current_id)
            )
        return out

    def apply(self, selections: Iterable[OrderSelection]) -> None:
        if not self._logged_in:
            self.login()
        for sel in selections:
            ref = self._slot_refs.get((sel.date, sel.slot))
            if ref is None:
                raise APIError(
                    f"no slot ref for {sel.date} {sel.slot!r}; "
                    "call list_menu() covering this date first"
                )
            self._put_switch(
                order_id=ref.order_id,
                delivery_id=ref.delivery_id,
                delivery_meal_id=ref.delivery_meal_id,
                diet_calories_meal_id=int(sel.dish_id),
            )

    def set_selection(self, day: date, slot: SlotName, dish_id: str) -> None:
        self.apply([OrderSelection(date=day, slot=slot, dish_id=dish_id)])

    def get_current_selection(self, day: date) -> list[OrderSelection]:
        out: list[OrderSelection] = []
        for (d, slot), _ref in self._slot_refs.items():
            if d != day:
                continue
            # current id was stored on the slot during list_menu
            # re-fetch is overkill; users should call list_menu first
        return out


# --- helpers ------------------------------------------------------------


def _is_day_published(slots: list[MenuSlot]) -> bool:
    """Treat a day as unpublished if every slot has only zero-kcal/empty options.

    The panel returns delivery shells for future dates with placeholder meals
    (no nutrition, name often null). Those days are skipped.
    """
    if not slots:
        return False
    for s in slots:
        if any(d.macros.kcal > 0 for d in s.options):
            return True
    return False


def _macros_from_nutrition(n: dict[str, Any]) -> Macros:
    return Macros(
        kcal=float(n.get("calories") or 0),
        protein=float(n.get("protein") or 0),
        fat=float(n.get("fat") or 0),
        carbs=float(n.get("carbohydrate") or 0),
    )


def _clean_allergens(values: list[Any] | None) -> list[str]:
    if not values:
        return []
    return [str(v) for v in values if v]


def _meal_to_dish(meal: dict[str, Any]) -> Dish:
    nutrition = meal.get("nutrition") or {}
    return Dish(
        id=str(meal["dietCaloriesMealId"]),
        name=meal.get("menuMealName") or "(bez nazwy)",
        macros=_macros_from_nutrition(nutrition),
        weight_g=nutrition.get("weight"),
        allergens=_clean_allergens(meal.get("allergens")),
    )


def _option_to_dish(opt: dict[str, Any]) -> Dish:
    details = opt.get("menuMealDetails") or {}
    nutrition = details.get("nutrition") or {}
    return Dish(
        id=str(details["dietCaloriesMealId"]),
        name=details.get("menuMealName") or "(bez nazwy)",
        macros=_macros_from_nutrition(nutrition),
        weight_g=nutrition.get("weight"),
        allergens=_clean_allergens(details.get("allergens")),
    )
