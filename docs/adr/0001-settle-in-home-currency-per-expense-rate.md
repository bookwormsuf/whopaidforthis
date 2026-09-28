# ADR 0001: Settle in the home currency, converting each expense at its own date's rate

Date: 2026-09-29. Status: accepted.

## Context
The brief proposed yen as the accounting currency with SGD as reporting. The friends will actually
repay each other in SGD after the trip, and some payments (card charges) are already in SGD.

## Decision
Balances and settlement are computed in the home currency. Each expense is converted once, at the
Frankfurter rate for its transaction date (falling back to the previous published rate), and that
rate is stored on the expense. Trip-currency expenses compute shares in trip minor units first so
they match the receipt, then convert and reconcile each share to the cent. Home-currency expenses
are never converted for settlement.

## Consequences
- Settlement figures are stable once an expense is saved; a later rate change never moves them.
- Two identical yen dinners on different days settle to slightly different SGD amounts. Accepted.
- The design is currency-neutral: any Frankfurter currency pair can be a trip.
- Rejected alternative: settle in yen and convert once at the end. Simpler arithmetic, but wrong for
  SGD card charges and it ignores the day-of-purchase rate the brief asked for.
