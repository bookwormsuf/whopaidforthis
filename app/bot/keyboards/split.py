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


def detected_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """`[Just me] [Everyone] [Choose people]`, then `[Cancel]` (sprint 5 pin)."""
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


def split_method_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """`[Equally] [Custom]` (`By items` arrives in Sprint 6)."""
    return InlineKeyboardMarkup(
        [
            [_button(draft_id, "Equally", "equal"), _button(draft_id, "Custom", "custom")],
            [_cancel(draft_id)],
        ]
    )


def custom_keyboard(draft_id: str, balanced: bool) -> InlineKeyboardMarkup:
    """`Confirm` is only rendered once the amounts add up exactly (sprint 5 pin)."""
    row = [_button(draft_id, "Confirm", "customok")] if balanced else []
    row.append(_cancel(draft_id))
    return InlineKeyboardMarkup([row])


def preview_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _button(draft_id, "Confirm", "confirm"),
                _button(draft_id, "Edit", "edit"),
                _button(draft_id, "Cancel", "cancel"),
            ]
        ]
    )


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
