import asyncio
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import api
from config import load_config, POLL_INTERVAL_SECONDS, TOPUP_THRESHOLD_PCT
from keystore import has_api_key
from paths import PRICE_HISTORY_FILE, ENGINE_STATE_FILE, LOG_FILE

# ---------------------------------------------------------------------------
# Logging — stdout for docker logs, rotating file for the dashboard UI
# Timestamps are always UTC ISO-8601 so the frontend can convert to any tz.
# ---------------------------------------------------------------------------
import time as _time

class _UTCFormatter(logging.Formatter):
    converter = _time.gmtime  # force UTC regardless of server locale
    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return _time.strftime("%Y-%m-%dT%H:%M:%SZ", ct)  # e.g. 2024-01-15T14:32:01Z

_fmt = _UTCFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger("hashbot.engine")

# ---------------------------------------------------------------------------
# Global scheduler reference — needed so trading_cycle can reschedule itself
# ---------------------------------------------------------------------------
_scheduler: AsyncIOScheduler | None = None
_current_poll_minutes: int = 0

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _load_price_history():
    try:
        import json as _j
        with open(PRICE_HISTORY_FILE) as f:
            data = _j.load(f)
        logger.info(f"Loaded {len(data)} price history entries from disk.")
        return data
    except Exception:
        return []


state = {
    "bid_id":               None,
    "current_price_sat":    None,
    "speed_limit_ph":       None,
    "amount_remaining_sat": None,
    "avg_speed_ph":         None,
    "last_updated":         None,
    "last_error":           None,
    "price_history":        [],
    "last_decrease_at":     None,
    "last_topup":           None,
}

# ---------------------------------------------------------------------------
# Fetch active bid
# ---------------------------------------------------------------------------
async def fetch_active_bid():
    data  = await api._request("GET", "/spot/bid/current")
    items = data.get("items", [])
    if not items:
        raise RuntimeError("No active bid found on the market.")

    item     = items[0]
    bid      = item["bid"]
    estimate = item.get("state_estimate", {})

    state["bid_id"]               = bid["id"]
    state["current_price_sat"]    = bid["price_sat"]
    state["speed_limit_ph"]       = bid["speed_limit_ph"]
    state["amount_remaining_sat"] = estimate.get("amount_remaining_sat", bid["amount_sat"])
    state["avg_speed_ph"]         = estimate.get("avg_speed_ph", 0)
    state["last_updated"]         = datetime.now(timezone.utc).isoformat()

    if state["last_decrease_at"] is None and bid.get("last_updated"):
        state["last_decrease_at"] = bid["last_updated"]

    logger.info(
        f"Active bid {state['bid_id']} | "
        f"price={state['current_price_sat']} sat/EH/day | "
        f"speed={state['avg_speed_ph']:.2f}/{state['speed_limit_ph']} PH | "
        f"remaining={state['amount_remaining_sat']} sat"
    )
    return state["bid_id"]

# ---------------------------------------------------------------------------
# Trading cycle
# ---------------------------------------------------------------------------
async def trading_cycle():
    global _current_poll_minutes

    logger.info("--- Trading cycle start ---")
    cfg = load_config()

    # ── Poll interval: reschedule if the user changed it ──────────────────
    desired_poll = max(1, int(cfg.get("poll_interval_seconds", POLL_INTERVAL_SECONDS)) // 60)
    if _scheduler and desired_poll != _current_poll_minutes:
        logger.info(f"Poll interval changed: {_current_poll_minutes}m → {desired_poll}m — rescheduling.")
        _scheduler.reschedule_job("trading_cycle", trigger="interval", minutes=desired_poll)
        _current_poll_minutes = desired_poll

    try:
        await fetch_active_bid()
        bid_id        = state["bid_id"]
        current_price = state["current_price_sat"]

        # ── Speed limit: apply user's config if it differs from live bid ──
        configured_speed = cfg.get("speed_limit_ph")
        live_speed       = state["speed_limit_ph"]
        speed_changed    = (
            configured_speed is not None
            and configured_speed != ""
            and float(configured_speed) != float(live_speed or 0)
        )

        # ── Target price from orderbook ───────────────────────────────────
        target_price = await api.get_nth_lowest_bid()
        if target_price is None:
            logger.warning("Could not determine target price. Skipping cycle.")
            return

        current_price  = state["current_price_sat"]
        price_changed  = target_price != current_price
        is_decrease    = target_price < current_price

        # Enforce 600s cooldown on price decreases (API requirement)
        if price_changed and is_decrease and state["last_decrease_at"] is not None:
            elapsed = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(state["last_decrease_at"])
            ).total_seconds()
            if elapsed < 600:
                wait = int(600 - elapsed)
                logger.info(
                    f"Price decrease blocked by cooldown. "
                    f"Wait {wait}s more before decreasing {current_price} → {target_price}."
                )
                # Still apply speed limit change if needed, without price change
                if speed_changed:
                    await _apply_speed_limit(bid_id, float(configured_speed))
                await maybe_topup_bid(bid_id, cfg)
                _record_price(current_price)
                return

        if not price_changed and not speed_changed:
            logger.info(f"Price unchanged at {current_price} sat/EH/day. No update needed.")
            await maybe_topup_bid(bid_id, cfg)
            _record_price(current_price)
            return

        # ── Build PUT payload with whatever changed ───────────────────────
        payload: dict = {"bid_id": bid_id}
        if price_changed:
            payload["new_price_sat"] = float(target_price)
            logger.info(f"Price change: {current_price} → {target_price} sat/EH/day")
        if speed_changed:
            payload["new_speed_limit_ph"] = float(configured_speed)
            logger.info(f"Speed limit change: {live_speed} → {configured_speed} PH/s")

        result = await api._request("PUT", "/spot/bid", json=payload)
        logger.info(f"Bid updated successfully: {result}")

        if price_changed:
            state["current_price_sat"] = target_price
            if is_decrease:
                state["last_decrease_at"] = datetime.now(timezone.utc).isoformat()
        if speed_changed:
            state["speed_limit_ph"] = float(configured_speed)

        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state["last_error"]   = None

        _record_price(state["current_price_sat"])
        await maybe_topup_bid(bid_id, cfg)
        _persist_state()

    except Exception as e:
        state["last_error"] = str(e)
        logger.error(f"Trading cycle error: {e}")


async def _apply_speed_limit(bid_id: str, speed_limit_ph: float):
    """Send only a speed limit update to the API (no price change)."""
    try:
        result = await api._request("PUT", "/spot/bid", json={
            "bid_id":             bid_id,
            "new_speed_limit_ph": speed_limit_ph,
        })
        state["speed_limit_ph"] = speed_limit_ph
        logger.info(f"Speed limit updated to {speed_limit_ph} PH/s: {result}")
    except Exception as e:
        logger.error(f"Speed limit update error: {e}")

# ---------------------------------------------------------------------------
# Auto top-up
# ---------------------------------------------------------------------------
async def maybe_topup_bid(bid_id: str, cfg: dict | None = None):
    """
    Top up the bid when remaining budget falls below the configured threshold.

    Uses amount_remaining_sat (always present) rather than progress_pct
    (sometimes missing) for the threshold check.

    Uses available_balance_sat (free funds not locked in any bid) rather than
    total_balance_sat (which includes the bid itself and would always fail the
    old comparison).
    """
    if cfg is None:
        cfg = load_config()

    threshold_pct = int(cfg.get("topup_threshold_pct", TOPUP_THRESHOLD_PCT))
    if threshold_pct <= 0:
        return

    try:
        # Re-use state populated by fetch_active_bid() — no extra API call needed
        amount_total     = state.get("amount_remaining_sat", 0) + 1  # fallback guard
        amount_remaining = state.get("amount_remaining_sat", 0)

        # Fetch fresh bid data for amount_sat (the original total budget)
        bid_data = await api._request("GET", "/spot/bid/current")
        items    = bid_data.get("items", [])
        if not items:
            return

        item         = items[0]
        bid          = item["bid"]
        estimate     = item.get("state_estimate", {})
        amount_total = bid["amount_sat"]

        # Use amount_remaining_sat directly — reliable, always present
        amount_remaining = estimate.get("amount_remaining_sat", amount_total)
        remaining_pct    = (amount_remaining / amount_total * 100) if amount_total > 0 else 100

        if remaining_pct > threshold_pct:
            return

        logger.info(
            f"Bid at {remaining_pct:.1f}% remaining "
            f"({amount_remaining:,} / {amount_total:,} sat) — "
            f"threshold {threshold_pct}% — checking for top-up..."
        )

        # Fetch account balance
        bal_data  = await api._request("GET", "/account/balance")
        accounts  = bal_data.get("accounts", [])
        if not accounts:
            logger.warning("No account data found.")
            return

        account = accounts[0]
        # available_balance_sat = free funds NOT locked in any bid
        available = int(account.get("available_balance_sat", 0))
        min_topup = 100_000  # API minimum

        if available < min_topup:
            logger.info(
                f"Available balance ({available:,} sat) below minimum ({min_topup:,} sat) — no top-up."
            )
            return

        # New bid amount = current remaining + available free balance
        new_amount = amount_remaining + available
        logger.info(
            f"Topping up bid {bid_id}: {amount_total:,} → {new_amount:,} sat "
            f"(adding {available:,} sat available balance)"
        )

        await api._request("PUT", "/spot/bid", json={
            "bid_id":         bid_id,
            "new_amount_sat": float(new_amount),
        })
        logger.info(f"Top-up successful: new amount = {new_amount:,} sat ({new_amount/1e8:.8f} BTC)")
        state["last_topup"] = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "old_amount": amount_total,
            "new_amount": new_amount,
        }

    except Exception as e:
        logger.error(f"Top-up error: {e}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _record_price(price_sat: int):
    state["price_history"].append({
        "ts":        datetime.now(timezone.utc).isoformat(),
        "price_sat": price_sat,
    })
    if len(state["price_history"]) > 131_040:
        state["price_history"] = state["price_history"][-131_040:]
    try:
        import json
        with open(PRICE_HISTORY_FILE, "w") as f:
            json.dump(state["price_history"], f)
    except Exception as e:
        logger.warning(f"Could not save price history: {e}")


def _persist_state():
    try:
        import json
        with open(ENGINE_STATE_FILE, "w") as f:
            json.dump({
                "last_decrease_at": state.get("last_decrease_at"),
                "last_error":       state.get("last_error"),
            }, f)
    except Exception as e:
        logger.warning(f"Could not save engine state: {e}")


def _sync_config_from_bid():
    """
    On first connect only: write bid_id and speed_limit_ph into config so the
    UI is pre-populated. Never overwrites values the user has already set.
    """
    try:
        from config import save_config
        cfg     = load_config()
        updates = {}
        if not cfg.get("bid_id"):
            updates["bid_id"] = state["bid_id"]
        # Only set speed_limit_ph if user hasn't configured one yet
        if not cfg.get("speed_limit_ph") and state.get("speed_limit_ph"):
            updates["speed_limit_ph"] = state["speed_limit_ph"]
        if updates:
            save_config(updates)
            logger.info(f"Config synced from live bid: {updates}")
    except Exception as e:
        logger.warning(f"Could not sync config from bid: {e}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    global _scheduler, _current_poll_minutes

    logger.info("Hashbot trading engine starting...")

    loaded = _load_price_history()
    state["price_history"] = loaded

    # Wait for API key
    if not has_api_key():
        logger.info("No API key configured yet — waiting. Set one via the dashboard UI.")
        while not has_api_key():
            await asyncio.sleep(10)
        logger.info("API key detected — starting engine.")

    try:
        await fetch_active_bid()
        logger.info(f"Tracking bid ID: {state['bid_id']}")
        _sync_config_from_bid()
    except Exception as e:
        logger.error(f"Failed to fetch active bid on startup: {e}")
        sys.exit(1)

    cfg                  = load_config()
    _current_poll_minutes = max(1, int(cfg.get("poll_interval_seconds", POLL_INTERVAL_SECONDS)) // 60)
    tz                   = cfg.get("timezone", "UTC")

    _scheduler = AsyncIOScheduler(timezone=tz)
    _scheduler.add_job(
        trading_cycle,
        "interval",
        id="trading_cycle",         # id required for reschedule_job
        minutes=_current_poll_minutes,
        next_run_time=datetime.now(timezone.utc),
    )
    _scheduler.start()
    logger.info(f"Scheduler started. Trading cycle runs every {_current_poll_minutes} minute(s).")

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Hashbot shutting down.")
        _scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
