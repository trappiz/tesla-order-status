import re
import webbrowser
from typing import List, Dict

from app.config import OPTION_CODES_URL, TELEMETRIC_URL, VERSION, cfg as Config
from app.utils.helpers import pseudonymize_data
from app.utils.params import DETAILS_MODE, SHARE_MODE, STATUS_MODE, CACHED_MODE, ALL_KEYS_MODE, ORDER_FILTER
from app.utils.connection import request_with_retry
from app.utils.locale import t, LANGUAGE, get_os_locale


def _normalize_option_code(raw_code: str) -> str:
    """Return a sanitized option code matching server expectations."""
    if not isinstance(raw_code, str):
        return ""
    trimmed = raw_code.strip().upper()
    if not trimmed or not re.fullmatch(r"[A-Z0-9]+", trimmed):
        return ""
    return trimmed[:32]


def _collect_option_codes(orders: List[dict]) -> List[str]:
    """Extract unique option codes from orders."""
    codes = set()
    for order in orders:
        order_data = order.get("order", {}) if isinstance(order, dict) else {}
        raw_options = order_data.get("mktOptions")
        if isinstance(raw_options, str):
            for part in raw_options.split(","):
                normalized = _normalize_option_code(part)
                if normalized:
                    codes.add(normalized)
    return sorted(codes)

def ensure_telemetry_consent() -> None:
    """Ask user for tracking consent if not already given."""
    if Config.has("telemetry-consent"):
        if Config.get("telemetry-consent"):
            return
        else:
            counter = Config.get("telemetry-consent-counter", 10) - 1
            if counter <= 0 or counter >10:
                counter = 10
                ask_for_telemetry_consent()
            Config.set("telemetry-consent-counter", counter)
    else:
        ask_for_telemetry_consent()

def ask_for_telemetry_consent() -> None:
    answer = input(
        t("Do you allow collection of non-personalised usage data to improve the script (press d for details)? (y/n/d): ")
    ).strip().lower()
    if answer == "d":
        webbrowser.open("https://github.com/chrisi51/tesla-order-status?tab=readme-ov-file#telemetry")
        ask_for_telemetry_consent()
        return
    consent = answer == "y"
    Config.set("telemetry-consent", consent)
    if answer == "y":
        print(t("Telemetry enabled. Thank you so much for your support, bro."))
    else:
        print(t("Naww, I've counted on you! =( I may ask again later, OK? =)"))
        input(t("Telemetry disabled. (ENTER): "))


def track_usage(orders: List[dict]) -> None:
    if not Config.get("telemetry-consent"):
        return

    # avoid circular dependency with orders module
    from app.utils.orders import get_model_from_order

    if not orders:
        user_orders = []
    else:
        user_orders: List[Dict[str, str]] = []
        for order in orders:
            ref = order.get("order", {}).get("referenceNumber")
            if ref:
                order_id = pseudonymize_data(ref, 16)
                model = get_model_from_order(order)

                user_orders.append(
                    {
                        "order_id": order_id,
                        "model": model
                    }
                )

    option_codes = _collect_option_codes(orders or [])

    params = {
        "details": DETAILS_MODE,
        "share": SHARE_MODE,
        "status": STATUS_MODE,
        "cached": CACHED_MODE,
        "all": ALL_KEYS_MODE,
        "filter": bool(ORDER_FILTER),
    }

    data = {
        "id": Config.get("fingerprint"),
        "orders": user_orders,
        "params": params,
        "lang": get_os_locale(),
        "ui_lang": LANGUAGE,
        "version": VERSION
    }

    try:
        request_with_retry(TELEMETRIC_URL, json=data, max_retries=3, exit_on_error=False)
    except Exception:
        # Telemetry failures should not impact the main application flow
        pass

    if option_codes:
        try:
            request_with_retry(
                OPTION_CODES_URL,
                json={"codes": option_codes},
                max_retries=3,
                exit_on_error=False
            )
        except Exception:
            # Swallow errors to keep endpoint stable
            pass
