"""The item card keyboard (spec §9, sprint 6 pins): one button per claimable item,
`"{n} {desc} {amount} · {initials}"`, one per row, then `Rest → everyone` | `Done`.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatting import fmt_signed_money
from app.services.drafts import (
    Draft,
    claimable_indices,
    person_for,
    unclaimed_indices,
    unclaimed_total,
)
from app.services.names import Person

_MAX_DESCRIPTION_CHARS = 24
EVERYONE_MARK = "all"


def initials_for(people: list[Person]) -> dict[int, str]:
    """First letter of the first name, uppercase; people whose letter collides get
    a second letter (the next letter of the first name, else the last name's
    initial), and any still-equal pair a number (sprint 6 pin).
    """

    def letters(person: Person) -> tuple[str, str]:
        words = person.display_name.split() or [str(person.user_id)]
        first = words[0]
        second = first[1:2] or (words[1][:1] if len(words) > 1 else "")
        return first[:1].upper(), second.lower()

    single = {p.user_id: letters(p)[0] for p in people}
    result: dict[int, str] = {}
    for person in people:
        one, two = letters(person)
        clash = [p for p in people if single[p.user_id] == one]
        result[person.user_id] = one if len(clash) == 1 else one + two
    seen: dict[str, int] = {}
    for person in sorted(people, key=lambda p: p.user_id):
        label = result[person.user_id]
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            result[person.user_id] = f"{label}{seen[label]}"
    return result


def draft_initials(draft: Draft) -> dict[int, str]:
    ids = list(dict.fromkeys([p.user_id for p in draft.people] + list(draft.participant_ids)))
    return initials_for([person_for(draft, uid) for uid in ids])


def item_button_text(draft: Draft, number: int, index: int, initials: dict[int, str]) -> str:
    item = draft.line_items[index]
    description = item.description
    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[: _MAX_DESCRIPTION_CHARS - 1] + "…"
    text = f"{number} {description} {fmt_signed_money(item.amount_minor, draft.currency)}"
    if index in draft.everyone_items:
        return f"{text} · {EVERYONE_MARK}"
    claimants = draft.item_claims.get(index, ())
    if claimants:
        return f"{text} · {''.join(initials[uid] for uid in claimants)}"
    return text


def tapping_for_rows(draft: Draft) -> list[list[InlineKeyboardButton]]:
    """The "Tapping for" row (added 2026-10-02): one button per joined person, ✓ on
    whoever the sender's item taps currently count for ("Me" for the sender). Only
    the sender can press these; with one person there is nothing to pick.
    """
    if len(draft.people) < 2:
        return []
    current = draft.claiming_for or draft.initiator_id
    buttons = []
    for person in draft.people:
        label = "Me" if person.user_id == draft.initiator_id else person.first_name
        if person.user_id == current:
            label = f"✓ {label}"
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"{draft.draft_id}:for:{person.user_id}")
        )
    return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]


def items_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    initials = draft_initials(draft)
    rows = [
        [
            InlineKeyboardButton(
                item_button_text(draft, number, index, initials),
                callback_data=f"{draft.draft_id}:item:{index}",
            )
        ]
        for number, index in enumerate(claimable_indices(draft), start=1)
    ]
    rows += tapping_for_rows(draft)
    if unclaimed_indices(draft) and not draft.rest_everyone:
        done = f"Done (unclaimed: {fmt_signed_money(unclaimed_total(draft), draft.currency)})"
    else:
        done = "Done"
    rows.append(
        [
            InlineKeyboardButton("Rest → everyone", callback_data=f"{draft.draft_id}:rest:-"),
            InlineKeyboardButton(done, callback_data=f"{draft.draft_id}:done:-"),
        ]
    )
    return InlineKeyboardMarkup(rows)
