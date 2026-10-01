"""The `Category` enum, shared by the text parser (§6) and receipt reading (§7)."""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    FOOD = "food"
    DRINKS = "drinks"
    TRANSPORT = "transport"
    ACCOMMODATION = "accommodation"
    SHOPPING = "shopping"
    ACTIVITIES = "activities"
    GROCERIES = "groceries"
    OTHER = "other"
