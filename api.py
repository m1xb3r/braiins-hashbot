import logging
import asyncio
import httpx
from config import BRAIINS_BASE_URL
from keystore import get_api_key
from paths import SETTINGS_FILE

logger = logging.getLogger(__name__)


class ScrubAPIKeyFilter(logging.Filter):
    """Scrub the API key from log records if it somehow appears."""
    def filter(self, record):
        key = get_api_key()
        if not key:
            return True
        msg = str(record.msg)
        if key in msg:
            record.msg = msg.replace(key, "***")
        if record.args:
            try:
                record.args = tuple(
                    str(a).replace(key, "***") if isinstance(a, str) else a
                    for a in record.args
                )
            except Exception:
                pass
        return True


logger.addFilter(ScrubAPIKeyFilter())


def _build_headers() -> dict:
    """Build headers with the current key fetched fresh from keystore."""
    key = get_api_key() or ""
    return {
        "apikey":       key,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


async def _request(method: str, path: str, **kwargs) -> dict:
    url     = f"{BRAIINS_BASE_URL}{path}"
    backoff = 2

    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(1, 6):
            try:
                response = await client.request(method, url, headers=_build_headers(), **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", backoff))
                    logger.warning(f"Rate limited. Waiting {retry_after}s (attempt {attempt}/5)")
                    await asyncio.sleep(retry_after)
                    backoff *= 2
                    continue

                if response.status_code >= 500:
                    logger.warning(f"Server error {response.status_code}. Retrying in {backoff}s (attempt {attempt}/5)")
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue

                if response.status_code >= 400:
                    logger.error(f"API error {response.status_code}: {response.text}")

                response.raise_for_status()
                return response.json()

            except httpx.RequestError as e:
                logger.error(f"Network error on attempt {attempt}/5: {e}")
                if attempt == 5:
                    raise
                await asyncio.sleep(backoff)
                backoff *= 2

    raise RuntimeError(f"All 5 attempts failed for {method} {path}")


async def get_orderbook() -> dict:
    return await _request("GET", "/spot/orderbook")


def _load_settings() -> dict:
    try:
        import json as _j
        with open(SETTINGS_FILE) as f:
            return _j.load(f)
    except Exception:
        return {"bid_rank": 5, "tick_offset": 1, "display_unit": "EH"}


async def get_nth_lowest_bid() -> float | None:
    settings  = _load_settings()
    rank      = int(settings.get("bid_rank", 5))
    tick_off  = int(settings.get("tick_offset", 1))
    tick_size = 1000

    data = await get_orderbook()
    bids = data.get("bids", [])
    if not bids:
        logger.error("No bids in orderbook.")
        return None

    active_bids = [b for b in bids if b.get("hr_matched_ph", 0) > 0]
    logger.info(f"Active bids: {len(active_bids)} of {len(bids)} total")

    if len(active_bids) < rank:
        logger.warning(f"Fewer than {rank} active bids ({len(active_bids)}), using all bids as fallback.")
        active_bids = bids

    sorted_bids  = sorted(active_bids, key=lambda b: b["price_sat"])
    idx          = min(rank - 1, len(sorted_bids) - 1)
    target       = sorted_bids[idx]
    ordinals     = {1: "1st", 2: "2nd", 3: "3rd"}
    ordinal      = ordinals.get(rank, f"{rank}th")
    offset       = tick_off * tick_size
    target_price = target["price_sat"] + offset

    logger.info(
        f"{ordinal} lowest active bid: {target['price_sat']} + {tick_off} tick(s) "
        f"({offset} sat) = {target_price} sat/EH/day "
        f"(matched: {target.get('hr_matched_ph', 0):.2f} PH)"
    )
    return target_price


async def place_bid(price_sat: int, speed_limit_ph: float, amount_sat: int, pool_id: str) -> dict:
    payload = {"price_sat": price_sat, "speed_limit_ph": speed_limit_ph, "amount_sat": amount_sat, "pool_id": pool_id}
    result = await _request("POST", "/spot/bid", json=payload)
    logger.info(f"Bid placed: {result}")
    return result


async def update_bid(order_id: str, price_sat: int, speed_limit_ph: float, amount_sat: int) -> dict:
    payload = {"order_id": order_id, "price_sat": price_sat, "speed_limit_ph": speed_limit_ph, "amount_sat": amount_sat}
    result = await _request("PUT", "/spot/bid", json=payload)
    logger.info(f"Bid updated: {result}")
    return result


async def cancel_bid(order_id: str) -> dict:
    result = await _request("DELETE", "/spot/bid", json={"order_id": order_id})
    logger.info(f"Bid cancelled: {order_id}")
    return result


async def get_bid_detail(order_id: str) -> dict:
    return await _request("GET", f"/spot/bid/detail/{order_id}")


async def list_active_bids() -> list:
    data = await _request("GET", "/spot/bid/current")
    return data.get("bids", [])


async def list_all_bids() -> list:
    data = await _request("GET", "/spot/bid")
    return data.get("bids", [])


async def get_bid_speed(order_id: str) -> dict:
    return await _request("GET", f"/spot/bid/speed/{order_id}")


async def get_transactions(limit: int = 100, offset: int = 0) -> list:
    data = await _request("GET", "/account/transaction", params={"limit": limit, "offset": offset})
    return data.get("transactions", [])


async def get_settings() -> dict:
    return await _request("GET", "/spot/settings")
