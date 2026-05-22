# PLAN — Saxo OpenAPI SIM Smoke Test

## Goal

Build a single standalone Python script that proves we can authenticate against Saxo Bank's **simulation (SIM) environment** and place a real test trade end-to-end. This is a one-off integration probe — not yet wired into the Stockdrop pipeline. If it works, a follow-up plan will turn it into a proper `app/services/saxo_service.py` alongside the existing `alpaca_service.py`.

**Out of scope:** the live (production) environment, real money, OAuth code-flow with refresh, multi-instrument logic, integration into the screener/PM pipeline. None of those are touched in this task.

## Deliverable

A single file: `scripts/saxo_sim_test.py`

Runnable as:

```bash
python scripts/saxo_sim_test.py
```

It should print a clear, readable transcript of each step (✅/❌ per stage) so a failure is easy to localize. Exit code 0 on full success, non-zero otherwise.

## Prerequisites the user (Simon) handles before running

1. Sign up for a free SIM developer account at https://www.developer.saxo/accounts/sim/signup. Credentials arrive by email.
2. Log into the developer portal and generate a **24-hour SIM access token** (the portal has a one-click "Get token" button for this — no app registration / OAuth flow needed for SIM).
3. Add the token to the project's `.env` file as `SAXO_SIM_TOKEN=<the token>`. Tokens expire in 24h — assume the user will refresh manually for now.

The script should fail fast with a clear message if `SAXO_SIM_TOKEN` is missing or empty. Do **not** prompt interactively for it.

## Scope — the smoke test

The script performs **one** linear flow:

1. **Auth check** — load the 24h token from `.env`, then `GET /sim/openapi/port/v1/users/me` to confirm the token works and print the user identity (`UserId`, `Name`, `ClientKey`).
2. **Account discovery** — `GET /sim/openapi/port/v1/accounts/me`. Pick the first account, print its `AccountKey`, `AccountId`, `Currency`, and balance. Save `AccountKey` for later.
3. **Instrument lookup** — resolve AAPL's Saxo `Uic` (numeric instrument ID) via `GET /sim/openapi/ref/v1/instruments?Keywords=AAPL&AssetTypes=Stock`. Filter for the NASDAQ-listed US common stock (the search returns multiple cross-listings — pick `ExchangeId == "NASDAQ"` and `AssetType == "Stock"`). Print and save the `Uic`.
4. **Quote** — `GET /sim/openapi/trade/v1/infoprices/?Uic=<uic>&AssetType=Stock&FieldGroups=Quote,PriceInfo`. Print bid/ask/last and the timestamp. This is read-only and confirms market data works.
5. **Place market order** — `POST /sim/openapi/trade/v2/orders` with the body shape below. Use a **small** amount (1 share) and `OrderDuration.DurationType = "DayOrder"`. Capture the returned `OrderId`.
6. **Read fill / status** — `GET /sim/openapi/port/v1/orders/me/?FieldGroups=DisplayAndFormat` and find our `OrderId`. Then check positions via `GET /sim/openapi/port/v1/positions/me/?FieldGroups=DisplayAndFormat,PositionBase`. Print whether the order is filled, working, or rejected, and whether a position exists.

Each step prints a header (`=== Step 3: Instrument lookup ===`), the raw HTTP status, and a concise summary. On any non-2xx response, print the response body and exit non-zero — do not continue downstream.

## Endpoints reference

Base URL for SIM: `https://gateway.saxobank.com/sim/openapi`

All requests carry `Authorization: Bearer <SAXO_SIM_TOKEN>` and `Content-Type: application/json`.

| Step | Method | Path |
|---|---|---|
| Auth check | GET | `/port/v1/users/me` |
| Accounts | GET | `/port/v1/accounts/me` |
| Instrument lookup | GET | `/ref/v1/instruments?Keywords=AAPL&AssetTypes=Stock` |
| Quote | GET | `/trade/v1/infoprices/?Uic={uic}&AssetType=Stock&FieldGroups=Quote,PriceInfo` |
| Place order | POST | `/trade/v2/orders` |
| List orders | GET | `/port/v1/orders/me/?FieldGroups=DisplayAndFormat` |
| Positions | GET | `/port/v1/positions/me/?FieldGroups=DisplayAndFormat,PositionBase` |

### Order placement body (Step 5)

```json
{
  "AccountKey": "<from step 2>",
  "Amount": 1,
  "AssetType": "Stock",
  "BuySell": "Buy",
  "OrderType": "Market",
  "OrderDuration": { "DurationType": "DayOrder" },
  "ManualOrder": true,
  "Uic": <from step 3>
}
```

Notes:
- `ManualOrder: true` is required by Saxo for orders not coming from a registered algo.
- US market hours matter even in SIM — outside market hours a market order may be rejected with an "InstrumentTradingStatus" error. If that happens, retry with a limit order well inside the spread (`OrderType: "Limit"`, `OrderPrice: <bid>`). The script should handle this fallback automatically and log which path was taken.

## Implementation guidelines

- **Language / runtime:** Python (matches `runtime.txt` — Python 3.9.6).
- **Dependencies:** `requests` and `python-dotenv` only. Both are already in the project.
- **Do NOT** add `saxo-openapi`, `saxo-apy`, or any wrapper library. Raw `requests` is fine for a smoke test and avoids dragging in a new dependency for a probe.
- **Structure:** one file, top-down. A small `SaxoSimClient` class with one method per step is fine, but it must stay in this single script — do not split into modules under `app/services/` yet. That's a follow-up after this works.
- **Type hints:** yes, per `CLAUDE.md`.
- **Async:** no. This is a CLI script, sync `requests` is appropriate. No asyncio.
- **Logging:** `print` is acceptable for this script; no need to wire it into the project's logger. Use `[STEP n]` / `[OK]` / `[FAIL]` prefixes for grep-ability.
- **Secrets:** read `SAXO_SIM_TOKEN` from `.env` via `python-dotenv`. Never hardcode. Never log the token (not even partially).

## Safety guardrails (must enforce in code)

1. Hardcode the base URL to `https://gateway.saxobank.com/sim/openapi`. Do **not** parameterize live vs SIM. If we ever need live, that's a deliberate, separate change.
2. Refuse to run if the resolved base URL does not start with `https://gateway.saxobank.com/sim/`. Sanity-check assert at startup.
3. Cap `Amount` at 1 share. No CLI flag to override. This is a smoke test, not a tool.
4. No retry loops on order placement. One attempt per order type (market → limit fallback if rejected for trading-status only). Any other failure: stop and print.

## Success criteria

The script run is successful if **all** of the following are true:
- All 6 steps print `[OK]`.
- Exit code is 0.
- The Saxo SIM web UI (login at https://www.saxotrader.com/sim) shows the order/position when the user logs in to verify.

## Open questions / things Claude Code should flag (do NOT silently guess)

- If the SIM account has multiple accounts (e.g. EUR + USD sub-accounts), which one to use? Default to the first; if there are more than one, print all of them and pick the USD account if present, otherwise the first.
- If AAPL Uic lookup returns zero NASDAQ matches, log the full response and exit. Do not fall back to a different ticker.
- If Saxo returns a 401 anywhere mid-run, surface the response body verbatim and remind the user the token may have expired (24h lifetime).

## After this works (next step, NOT in this task)

Once the smoke test passes:
1. Promote `SaxoSimClient` to `app/services/saxo_service.py`.
2. Add OAuth code-flow auth so we don't need to refresh tokens manually every 24h.
3. Add a paper-trading toggle so the existing decision pipeline can shadow-trade BUY/BUY_LIMIT verdicts to SIM and we can compare Saxo SIM fills against the model's predictions.

---

**Reference:** Saxo OpenAPI docs at https://www.developer.saxo/openapi/learn/welcome and order placement reference at https://developer.saxobank.com/openapi/learn/order-placement.
