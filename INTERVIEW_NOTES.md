# Interview notes

Talking points for this project, filled in phase by phase.

## The 30-second pitch

I built a product and marketing analytics project on a GA4-schema ecommerce event export, run
locally on DuckDB. It covers a sessionized SQL library, a checkout funnel with significance
tests, five attribution models, a revenue metric tree with day-over-day decomposition, KPI
anomaly detection, a Streamlit dashboard and a stakeholder readout.

## Why synthetic data, and why that is a strength

- The brief named Google's public GA4 sample, which needs a Google Cloud project to read. I
  chose not to use GCP, so I wrote a generator that reproduces the GA4 export schema exactly:
  nested `event_params` and `items`, `ga_session_id`, user-level `traffic_source`.
- Because I control the generator, I planted effects with known size. That lets me validate the
  analysis: if the funnel test, attribution or anomaly detector misses a planted effect, the
  method is wrong. On the real export there is no ground truth to check against.
- The honest limit: findings describe the planted world, not the Google Merchandise Store.

## Data generation

- **Schema fidelity:** same nested columns and daily-file layout as the BigQuery export. Session
  source/medium sits in `event_params`, and `traffic_source` is first touch only. That is the
  classic GA4 trap: people attribute sessions to `traffic_source` and silently get first-touch
  attribution for every session.
- **Ground truth:** five planted effects with known size (see `docs/DATA.md`). Each later phase
  has to recover one.
- **Defects on purpose:** placeholders, `(not set)` transaction ids, duplicate purchases, NULL
  revenue, missing session ids. These are the problems you hit in the real obfuscated export, so
  the SQL has to handle them rather than assume clean data.
- **Likely question: "Isn't synthetic data cheating?"** It's a validation harness. On real data
  you can't tell whether a Markov model or an anomaly detector is right; here I can check it
  against the planted truth. The trade-off is that real data has messier, unknown effects, and I
  say so in the readout.

## SQL library and sessionization

- **Session key** = `user_pseudo_id` + `ga_session_id`. `ga_session_id` alone is a timestamp,
  not unique across users, which is a common mistake.
- **Session channel from `event_params`, not `traffic_source`.** `traffic_source` is first-touch
  user acquisition; using it for sessions makes every report first-click by accident.
- **Repairing missing session ids:** gaps-and-islands with `last_value(... IGNORE NULLS)` looking
  back and `first_value` looking forward, within GA4's 30-minute timeout.
- **Purchase dedup:** `row_number()` over transaction id, falling back to session + basket value
  when the id is `(not set)`. Revenue is repaired from item revenue when the purchase total is NULL.
- **Open vs closed funnel:** open = the event happened; closed = the steps happened in order.
  The gap between them is a tracking-quality signal.
- **Performance story:** correlated `UNNEST` per key took 49 s; one `UNNEST` + conditional
  aggregation took 3.8 s (13x). On BigQuery the same change cuts slot time, and bytes billed stay
  the same because the same columns are read.
- **Dry run first:** the runner reads DuckDB's plan, applies the pushed-down `_TABLE_SUFFIX` file
  filter and sums the compressed bytes of only the projected columns, which is the same idea as
  BigQuery's bytes processed.

## Funnel and significance testing

- **Headline:** mobile converts 38% worse than desktop, and the step-by-step tests put all of it at
  payment and purchase. Browsing and add to cart are identical. That turns "mobile is worse" into
  "fix the mobile payment form", which is actionable.
- **Why step tests, not just overall:** the overall test says *whether* there is a gap; testing
  each step's conditional rate says *where*. Holm-correct across the six steps.
- **Holm over Bonferroni:** same family-wise guarantee, uniformly more power.
- **Effect sizes:** with 139k sessions almost everything is significant, so I report relative lift
  and Cohen's h, and the MDE for the small segments.
- **Sessions are not independent:** a user-clustered bootstrap (Poisson weights, which handle
  users in both groups, e.g. new and returning). Here the design effect is about 1 because the
  generator has no user-level propensity. I say that openly; on real data I would expect more than
  1 and would trust the bootstrap over the z-test.
- **Open vs closed funnel:** 344 purchases are in the open funnel but not the closed one, because
  checkout events went missing in sessions that still bought. That's a tracking issue to raise
  with engineering, not a user behaviour.
- **Likely question: "Isn't channel conversion channel value?"** No. Display converts at 1.2% per
  session but opens journeys that convert later through Direct or Email. That's what attribution
  (phase 5) is for.

## Attribution

- **One-line answer to "why do they disagree?":** channels play different roles in a journey, and
  each model encodes a different assumption about which role deserves credit. Last and first click
  are rules about position; Markov asks how much conversion probability flows through a channel;
  Shapley asks for a fair split of a game you have to define.
- **Concrete example:** Display gets 3.6% under last-click and 8.7% under first-click, because it
  opens 88% of the journeys where it is at an end. Email is the mirror image: 16.2% last-click,
  4.1% first-click.
- **Markov removal effect:** remove a channel by sending its visits to NULL, recompute
  P(conversion from START) from the absorbing-chain fundamental matrix, then normalise the
  relative drops. Effects sum to more than 100% because routes overlap.
- **Shapley insight (strong talking point):** with v(S) = conversions of journeys using only
  channels in S, each journey is a unanimity game, so the Shapley value reduces *exactly* to an equal
  split over distinct channels, i.e. linear attribution. Using a rate game instead gives prospecting
  channels negative credit. The characteristic function is the model.
- **Uncertainty:** Poisson-bootstrap intervals are about ±1 point, while the gaps between models are
  up to 12 points. More data won't settle it; only an experiment can.
- **Causality caveat:** none of these models say what would happen without a channel. For budget
  decisions, run a geo holdout or conversion-lift test.
- **Data caveats:** cookie-level ids split cross-device journeys, and the window cuts off journeys
  that started before November or were still open at the end of January.
