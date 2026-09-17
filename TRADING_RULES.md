# Teddy's Trading Criteria

These are permanent, fail-closed eligibility rules for Mosquito. Momentum can rank a company only after these rules pass.

1. Use the most recent six months of market history; do not rank on a five-year window.
2. A company must have positive net assets and positive shareholder equity.
3. Debt is allowed only when it is covered by cash or by a clear asset cushion. Mosquito currently defines a clear cushion as assets at least 1.20 times liabilities, while total debt must never exceed assets.
4. Reject an overleveraged company, a company with a negative balance, or a company whose required balance-sheet evidence is unavailable.
5. Confirm a positive production backlog, order book, remaining performance obligation, or contracted-revenue balance from the latest available SEC filing. Missing or ambiguous evidence is a rejection.
6. Apply this fundamental gate before a symbol becomes tradable. A high momentum score cannot override a failure.
7. Never voluntarily sell below the complete filled purchase cost. After a profitable sale, keep the symbol on the re-entry watch list and buy it again only after a new qualifying rise.

The dashboard must expose the gate result and its evidence for selected symbols. These rules are paper-trading controls; they are not a guarantee of profit.

## Isolated reversal research

The prior-day reversal screen is research/shadow output only. It uses completed
sessions strictly before the proposed trade date, caps results at 25, and cannot
submit broker orders or replace V5.8. Its filters are: negative 126-session
return, positive five-session return, positive prior-session return, and a
positive gap on the prior session. Trading costs and fills must be modeled before
interpreting any historical result; profit is not guaranteed.

V5.8 ranking also applies the same completed-session equation as an auditable
10% soft overlay. A stock receives an overlay percentile only when all four
reversal conditions are true; qualifying six-month losses closer to zero score
higher. Nonmatches receive zero, preserving ranking breadth. The other 90% is
the existing V5.8 momentum/volume/quality score. The SEC fundamentals gate
remains fail-closed after ranking and before a symbol can become paper-tradable.
