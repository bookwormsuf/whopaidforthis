"""The item-by-item keyboards (sprint 13 pins): "Who had this?" (a toggle per
participant, `Everyone`, `Not on this bill`, `Back` / `Next`) and the `Fix an item`
list, one button per claimable item, `"{n} {desc} {amount} · {initials}"`.
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardMarkup

from app.bot.formatting import fmt_signed_money
from app.bot.keyboards.split import _button, _cancel, short_name
from app.services.drafts import Draft, claimable_indices, claimants_on_screen, person_for
from app.services.names import Person

_MAX_DESCRIPTION_CHARS = 24
EVERYONE_MARK = "all"
LEFT_OUT_MARK = "—"
LEFT_OUT_LABEL = "🚫 Not on this bill"
_UNIT_SUFFIX = re.compile(r" \(\d+/\d+\)$")


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
        # Keep a unit's "(1/3)": it is what tells the units of a line apart.
        match = _UNIT_SUFFIX.search(description)
        suffix = match.group(0) if match else ""
        keep = max(_MAX_DESCRIPTION_CHARS - len(suffix) - 1, 1)
        description = description[: len(description) - len(suffix)][:keep] + "…" + suffix
    text = f"🍽 {number} {description} {fmt_signed_money(item.amount_minor, draft.currency)}"
    if index in draft.left_out:
        return f"{text} · {LEFT_OUT_MARK}"
    claimants = draft.item_claims.get(index, ())
    everyone = set(draft.participant_ids)
    if index in draft.everyone_items or (len(everyone) > 1 and everyone <= set(claimants)):
        return f"{text} · {EVERYONE_MARK}"
    if claimants:
        return f"{text} · {''.join(initials[uid] for uid in claimants)}"
    return text


def walk_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    """One toggle per participant, three per row, `✓ ` when ticked; `Everyone`;
    `Not on this bill` (sprint 14); `◀ Back` (not on the first item, unless opened
    from `Fix an item`) and `Next ▶`; `Cancel`. Every button carries the item's
    position, so a press from an older render changes nothing (sprint 13 pin).
    """
    position = draft.walk_index
    index = claimable_indices(draft)[position]
    ticked = set(claimants_on_screen(draft, index))
    people = [person_for(draft, pid) for pid in draft.participant_ids]
    toggles = [
        _button(
            draft.draft_id,
            ("✓ " if person.user_id in ticked else "") + short_name(person, people),
            "wtoggle",
            f"{position},{person.user_id}",
        )
        for person in people
    ]
    rows = [toggles[i : i + 3] for i in range(0, len(toggles), 3)]
    rows.append([_button(draft.draft_id, "Everyone", "weveryone", str(position))])
    rows.append([_button(draft.draft_id, LEFT_OUT_LABEL, "wleftout", str(position))])
    moves = []
    if position > 0 or draft.fixing_one:
        moves.append(_button(draft.draft_id, "◀ Back", "wback", str(position)))
    moves.append(_button(draft.draft_id, "Next ▶", "wnext", str(position)))
    rows.append(moves)
    rows.append([_cancel(draft.draft_id)])
    return InlineKeyboardMarkup(rows)


def fix_list_keyboard(draft: Draft) -> InlineKeyboardMarkup:
    """`Fix an item`: one button per claimable item with who has it, then
    `◀ Back to summary`.
    """
    initials = draft_initials(draft)
    rows = [
        [
            _button(
                draft.draft_id,
                item_button_text(draft, number, index, initials),
                "fixpick",
                str(number - 1),
            )
        ]
        for number, index in enumerate(claimable_indices(draft), start=1)
    ]
    rows.append([_button(draft.draft_id, "◀ Back to summary", "fixback", "-")])
    return InlineKeyboardMarkup(rows)
