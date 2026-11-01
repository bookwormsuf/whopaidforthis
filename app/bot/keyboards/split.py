"""Inline keyboards for the sharing steps of a draft (`detected`, `resolve_name`,
`participants`, `split_method`, `custom`, `preview`). Spec references:
pinned-decisions §8, sprint 5 pins. Callback data is `"{draft_id}:{action}:{arg}"`.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.drafts import Draft, PendingName, person_for
from app.services.names import Person

NOT_SURE_LABEL = "Not sure, choose people"


def _button(draft_id: str, label: str, action: str, arg: str = "-") -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"{draft_id}:{action}:{arg}")


def _cancel(draft_id: str) -> InlineKeyboardButton:
    return _button(draft_id, "Cancel", "cancel")


def short_name(person: Person, people) -> str:
    """The first name, or the full name when another person shares the first name."""
    same = [p for p in people if p.first_name.casefold() == person.first_name.casefold()]
    return person.display_name if len(same) > 1 else person.first_name


def detected_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    """`[Just me] [Everyone] [Choose people]`, then `[Cancel]` (sprint 5 pin)."""
    draft_id = draft.draft_id
    return InlineKeyboardMarkup(
        [
            [
                _button(draft_id, "Just me", "justme"),
                _button(draft_id, "Everyone", "everyone"),
                _button(draft_id, "Choose people", "choose"),
            ],
            [_cancel(draft_id)],
        ]
    )


def who_paid_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    """One button per joined person, the sender as "Me", three per row, then
    `[Cancel]` (decided 2026-10-03).
    """
    buttons = []
    for person in draft.people:
        label = short_name(person, draft.people)
        if person.user_id == draft.initiator_id:
            label = "Me"
        buttons.append(_button(draft.draft_id, label, "payer", str(person.user_id)))
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([_cancel(draft.draft_id)])
    return InlineKeyboardMarkup(rows)


def participants_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    """One toggle per joined user, two per row, ✓ when selected; then `Next`."""
    selected = set(draft.participant_ids)
    toggles = [
        _button(
            draft.draft_id,
            f"✓ {person.display_name}" if person.user_id in selected else person.display_name,
            "toggle",
            str(person.user_id),
        )
        for person in draft.people
    ]
    rows = [toggles[i : i + 2] for i in range(0, len(toggles), 2)]
    rows.append([_button(draft.draft_id, "Next", "next"), _cancel(draft.draft_id)])
    return InlineKeyboardMarkup(rows)


def split_method_keyboard(draft_id: str, by_items: bool) -> InlineKeyboardMarkup:
    """One per row: `🍽 Go item by item ▶` (only for 1 to 40 claimable items),
    `Split equally`, `Type amounts instead`, `Cancel` (sprint 13 pin).
    """
    rows = []
    if by_items:
        rows.append([_button(draft_id, "🍽 Go item by item ▶", "walk")])
    rows.append([_button(draft_id, "Split equally", "equal")])
    rows.append([_button(draft_id, "Type amounts instead", "custom")])
    rows.append([_cancel(draft_id)])
    return InlineKeyboardMarkup(rows)


def custom_keyboard(draft_id: str, balanced: bool) -> InlineKeyboardMarkup:
    """`Confirm` is only rendered once the amounts add up exactly (sprint 5 pin)."""
    row = [_button(draft_id, "Confirm", "customok")] if balanced else []
    row.append(_cancel(draft_id))
    return InlineKeyboardMarkup([row])


def preview_keyboard(
    draft_id: str, *, retry: bool = False, by_items: bool = False
) -> InlineKeyboardMarkup:
    """`Confirm / Edit / Cancel`; an itemised preview (sprint 13) has `Confirm` and
    `Fix an item` on the first row, `Edit` and `Cancel` on the second.

    `retry=True` (spec §10/§14: a failed save keeps the draft in `preview` with
    a `Retry save` button) relabels Confirm without changing its callback action,
    so pressing it simply calls `confirm` again -- no new state needed.
    """
    confirm = _button(draft_id, "Retry save" if retry else "Confirm", "confirm")
    if by_items:
        return InlineKeyboardMarkup(
            [
                [confirm, _button(draft_id, "Fix an item", "fixlist")],
                [_button(draft_id, "Edit", "edit"), _cancel(draft_id)],
            ]
        )
    return InlineKeyboardMarkup([[confirm, _button(draft_id, "Edit", "edit"), _cancel(draft_id)]])


def _candidate_label(person: Person, others: list[Person]) -> str:
    """The display name, plus @username when another candidate shares the name."""
    same = [p for p in others if p.display_name.casefold() == person.display_name.casefold()]
    if len(same) > 1 and person.username:
        return f"{person.display_name} (@{person.username})"
    return person.display_name


def resolve_name_keyboard(draft: Draft, pending: PendingName) -> InlineKeyboardMarkup:
    """One button per candidate, then the `Not sure` way out."""
    people = [person_for(draft, pid) for pid in pending.candidate_ids]
    candidates = [
        _button(draft.draft_id, _candidate_label(person, people), "pick", str(person.user_id))
        for person in people
    ]
    rows = [candidates[i : i + 2] for i in range(0, len(candidates), 2)]
    rows.append([_button(draft.draft_id, NOT_SURE_LABEL, "unsure")])
    rows.append([_cancel(draft.draft_id)])
    return InlineKeyboardMarkup(rows)
