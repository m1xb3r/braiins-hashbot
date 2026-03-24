import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import httpx
import uvicorn

import api
from main import state, trading_cycle
from config import DASHBOARD_HOST, DASHBOARD_PORT, BRAIINS_BASE_URL, load_config
from keystore import get_api_key, save_api_key, delete_api_key, has_api_key, mask_key
from paths import PRICE_HISTORY_FILE, ENGINE_STATE_FILE, SETTINGS_FILE, LOG_FILE

logger    = logging.getLogger("hashbot.dashboard")
app       = FastAPI(title="Hashbot Dashboard")
# Templates resolved relative to this file so it works in any working directory
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def get_status():
    try:
        data  = await api._request("GET", "/spot/bid/current")
        items = data.get("items", [])
        if not items:
            return {"error": "No active bid found"}

        item     = items[0]
        bid      = item["bid"]
        estimate = item.get("state_estimate", {})
        counters = item.get("counters_estimate", {})

        ob = await api.get_orderbook()
        asks = ob.get("asks", [])
        active_asks = sorted(
            [a for a in asks if a.get("hr_matched_ph", 0) > 0],
            key=lambda a: a["price_sat"]
        )
        if len(active_asks) < 5:
            active_asks = sorted(asks, key=lambda a: a["price_sat"])
        fifth_ask = active_asks[4]["price_sat"] if len(active_asks) >= 5 else None

        import json as _json
        try:
            with open(PRICE_HISTORY_FILE) as f:
                history = _json.load(f)
        except Exception:
            history = []

        avg_price = (
            int(sum(p["price_sat"] for p in history) / len(history))
            if history else bid["price_sat"]
        )

        try:
            with open(ENGINE_STATE_FILE) as f:
                eng = _json.load(f)
            last_dec = eng.get("last_decrease_at")
        except Exception:
            last_dec = state.get("last_decrease_at")
        cooldown_remaining = 0
        if last_dec:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_dec)).total_seconds()
            cooldown_remaining = max(0, int(600 - elapsed))

        bal_data = await api._request("GET", "/account/balance")
        accounts = bal_data.get("accounts", [])
        account  = accounts[0] if accounts else {}

        return {
            "bid_id":                bid["id"],
            "total_balance_sat":     account.get("total_balance_sat", 0),
            "available_balance_sat": account.get("available_balance_sat", 0),
            "blocked_balance_sat":   account.get("blocked_balance_sat", 0),
            "status":                bid["status"],
            "current_price_sat":     bid["price_sat"],
            "fifth_bid_sat":         fifth_ask,
            "avg_price_sat":         avg_price,
            "speed_limit_ph":        bid["speed_limit_ph"],
            "avg_speed_ph":          estimate.get("avg_speed_ph", 0),
            "amount_sat":            bid["amount_sat"],
            "amount_remaining_sat":  estimate.get("amount_remaining_sat", 0),
            "progress_pct":          estimate.get("progress_pct", 0),
            "amount_consumed_sat":   counters.get("amount_consumed_sat", 0),
            "last_updated":          bid["last_updated"],
            "last_error":            state.get("last_error"),
            "last_topup":            state.get("last_topup"),
            "cooldown_remaining_s":  cooldown_remaining,
            "price_history":         history[-48:],
            "dest":                  bid.get("dest_upstream", {}).get("url", ""),
            "last_pause_reason":     bid.get("last_pause_reason", ""),
        }
    except Exception as e:
        logger.error(f"Dashboard status error: {e}")
        return {"error": str(e)}


@app.get("/api/orderbook")
async def get_orderbook_snapshot():
    try:
        data = await api.get_orderbook()
        asks = sorted(data.get("asks", []), key=lambda a: a["price_sat"])
        bids = sorted(data.get("bids", []), key=lambda b: b["price_sat"], reverse=True)
        return {"asks": asks[:10], "bids": bids[:10]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/price-history")
async def get_price_history(period: str = "1d"):
    import json
    try:
        with open(PRICE_HISTORY_FILE) as f:
            all_entries = json.load(f)
    except Exception:
        return {"entries": [], "avg_price_sat": None, "period": period}

    if not all_entries:
        return {"entries": [], "avg_price_sat": None, "period": period}

    now     = datetime.now(timezone.utc)
    periods = {
        "1d": (timedelta(days=1),   288,  "24 Hours"),
        "1w": (timedelta(weeks=1),  336,  "7 Days"),
        "1m": (timedelta(days=30),  360,  "30 Days"),
        "6m": (timedelta(days=182), 360,  "6 Months"),
    }
    if period not in periods:
        period = "1d"
    delta, max_points, label = periods[period]
    cutoff = now - delta

    filtered = []
    for e in all_entries:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            if ts >= cutoff:
                filtered.append({"ts": e["ts"], "price_sat": e["price_sat"], "dt": ts})
        except Exception:
            continue

    if not filtered:
        last = all_entries[-1]
        return {"entries": [last], "avg_price_sat": last["price_sat"],
                "min_price_sat": last["price_sat"], "max_price_sat": last["price_sat"],
                "period": period, "label": label, "data_points": 1}

    if len(filtered) > max_points:
        step = len(filtered) / max_points
        downsampled = []
        i = 0.0
        while i < len(filtered):
            b_start = int(i); b_end = min(int(i + step), len(filtered))
            bucket  = filtered[b_start:b_end]
            avg_p   = sum(e["price_sat"] for e in bucket) // len(bucket)
            downsampled.append({"ts": bucket[len(bucket) // 2]["ts"], "price_sat": avg_p})
            i += step
        result = downsampled
    else:
        result = [{"ts": e["ts"], "price_sat": e["price_sat"]} for e in filtered]

    prices = [e["price_sat"] for e in result]
    return {"entries": result, "avg_price_sat": sum(prices) // len(prices),
            "min_price_sat": min(prices), "max_price_sat": max(prices),
            "period": period, "label": label, "data_points": len(result)}


@app.get("/api/log")
async def get_log():
    """Return last 30 relevant lines from the shared log file."""
    try:
        if not LOG_FILE.exists():
            return {"lines": []}

        # Read last ~8KB to avoid loading huge files
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")

        key = get_api_key() or ""
        keywords = ("Trading cycle", "Price", "Bid updated", "cooldown",
                    "Active bid", "lowest", "ERROR", "error", "Top-up",
                    "top-up", "Key detected", "starting")
        lines = []
        for line in tail.splitlines():
            if not any(kw in line for kw in keywords):
                continue
            if key and key in line:
                line = line.replace(key, "***")
            # Format: "2024-01-15T14:32:01Z [INFO] name: message"
            # Split on first space to separate ISO timestamp from the rest
            parts = line.split(" ", 1)
            ts  = parts[0] if len(parts) >= 1 else ""   # ISO UTC string
            msg = parts[1] if len(parts) == 2 else line
            lines.append({"ts": ts, "msg": msg})

        return {"lines": lines[-30:]}
    except Exception as e:
        return {"lines": [{"ts": "", "msg": f"Log unavailable: {e}"}]}


@app.get("/api/settings")
async def get_settings_api():
    import json
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"bid_rank": 5, "tick_offset": 1, "display_unit": "EH", "timezone": "Europe/Berlin"}


@app.post("/api/settings")
async def save_settings_api(request: Request):
    import json
    try:
        data     = await request.json()
        rank     = max(1, min(20, int(data.get("bid_rank", 5))))
        tick     = int(data.get("tick_offset", 1))
        unit     = data.get("display_unit", "EH")
        timezone = data.get("timezone", "Europe/Berlin").strip() or "Europe/Berlin"
        if unit not in ("EH", "PH"):
            unit = "EH"
        settings = {"bid_rank": rank, "tick_offset": tick, "display_unit": unit, "timezone": timezone}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        # Also persist timezone to config.json so the engine picks it up
        try:
            from config import save_config as _save_cfg
            _save_cfg({"timezone": timezone})
        except Exception:
            pass
        return {"ok": True, "settings": settings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/cycle")
async def trigger_cycle():
    try:
        await trading_cycle()
        return {"ok": True, "message": "Cycle completed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Setup status ──────────────────────────────────────────────────────────

@app.get("/api/setup-status")
async def setup_status():
    """Lightweight poll — tells the UI whether the app is ready to trade."""
    return {"ready": has_api_key()}


# ── Credentials ──────────────────────────────────────────────────────────

@app.get("/api/credentials")
async def get_credentials():
    """Return whether a key is set and a masked preview. Never returns the key."""
    key = get_api_key()
    if not key:
        return {"has_key": False, "key_preview": ""}
    return {"has_key": True, "key_preview": mask_key(key)}


@app.post("/api/credentials")
async def save_credentials(request: Request):
    """
    Validate the submitted key against Braiins, then encrypt and persist it.
    The plaintext key is held in memory only for the duration of this request.
    """
    try:
        body    = await request.json()
        new_key = (body.get("api_key") or "").strip()
        if not new_key:
            return {"ok": False, "error": "api_key is required"}

        # Validate against Braiins before saving anything
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BRAIINS_BASE_URL}/spot/settings",
                headers={"apikey": new_key, "Accept": "application/json"},
            )

        if r.status_code in (401, 403):
            return {"ok": False, "error": "Invalid API key — Braiins rejected it"}
        if r.status_code != 200:
            return {"ok": False, "error": f"Unexpected response from Braiins: {r.status_code}"}

        # Encrypt and persist — plaintext never touches disk
        save_api_key(new_key)
        return {"ok": True, "key_preview": mask_key(new_key)}

    except Exception as e:
        logger.error(f"save_credentials error: {e}")
        return {"ok": False, "error": str(e)}


@app.delete("/api/credentials")
async def remove_credentials():
    """Securely wipe the encrypted key file."""
    try:
        delete_api_key()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}



if __name__ == "__main__":
    uvicorn.run("dashboard:app", host=DASHBOARD_HOST, port=DASHBOARD_PORT, reload=False, log_level="info")
