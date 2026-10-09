"""Publish only validated public Moscow minima; never collect driver locations."""

import datetime as dt
import decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

SOURCE = "https://taxi.yandex.ru/moscow/tariff/"
LIMIT = 1_048_576
IDS = {"econom", "business", "comfortplus", "vip", "ultimate", "maybach",
       "child_tariff", "minivan", "premium_van", "intercity"}
MINIMUM = "taximeter.min_price_included_distance_and_time"
MARKER = "__init__.default("


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def extract(raw: bytes, now: dt.datetime) -> bytes:
    if len(raw) > LIMIT:
        raise ValueError("Source too large")
    html = raw.decode("utf-8", errors="strict")
    dates = re.findall(r'data-key="tariff\.disclaimer\.expiry\.date"[^>]*>[^<]*?'
                       r'(\d{2}\.\d{2}\.\d{4})\.?\s*</p>', html)
    if len(dates) != 1:
        raise ValueError("Ambiguous expiry")
    expiry = dt.datetime.strptime(dates[0], "%d.%m.%Y").date()
    if expiry < now.astimezone(ZoneInfo("Europe/Moscow")).date():
        raise ValueError("Source expired")
    if html.count(MARKER) != 1:
        raise ValueError("Ambiguous source payload")
    payload = html.split(MARKER, 1)[1].split("</script>", 1)[0].strip().removesuffix(";")
    if not payload.endswith(")"):
        raise ValueError("Invalid source payload")
    zone = json.loads(payload[:-1], object_pairs_hook=unique_object)["initialState"]["zonaltariffdescription"]
    if (zone["zoneName"] != "moscow" or zone["isZoneUnsupported"] is not False
            or zone["currentCategoryType"] != "application" or zone["currency_rules"]["code"] != "RUB"):
        raise ValueError("Unsupported tariff zone")
    tariffs = zone["max_tariffs"]
    if not isinstance(tariffs, list) or len(tariffs) > 100:
        raise ValueError("Invalid tariff list")
    clean, seen = [], set()
    for tariff in tariffs:
        tariff_id = tariff.get("id")
        if tariff_id not in IDS:
            continue
        if tariff_id in seen or tariff.get("class") != tariff_id:
            raise ValueError("Ambiguous tariff")
        seen.add(tariff_id)
        intervals = [i for i in tariff.get("intervals", []) if i.get("category_type") == "application"]
        if len(intervals) != 1:
            raise ValueError("Ambiguous application interval")
        minima = [row.get("price") for group in intervals[0].get("price_groups", [])
                  if group.get("id") == "free_route" for row in group.get("prices", [])
                  if row.get("id") == MINIMUM]
        if not minima:
            continue
        if len(minima) != 1:
            raise ValueError("Ambiguous minimum")
        if len(str(minima[0])) > 100:
            raise ValueError("Minimum too long")
        match = re.fullmatch(r'(?:от\s+)?([0-9][0-9 \u00a0\u202f]*(?:[.,][0-9]+)?)'
                             r'(?:\s*(?:руб\.?|₽|RUB|\$SIGN\$\$CURRENCY\$))?', str(minima[0]).strip())
        if not match:
            raise ValueError("Invalid minimum")
        amount = decimal.Decimal(re.sub(r'[ \u00a0\u202f]', '', match[1]).replace(',', '.'))
        if not 0 < amount <= 1_000_000_000 or amount != amount.quantize(decimal.Decimal("0.01")):
            raise ValueError("Invalid minimum amount")
        clean.append({"id": tariff_id, "class": tariff_id, "intervals": [{"category_type": "application",
            "price_groups": [{"id": "free_route", "prices": [{"id": MINIMUM, "price": str(amount)}]}]}]})
    if not {"econom", "business", "comfortplus"}.issubset({t["id"] for t in clean}):
        raise ValueError("Core minima missing")
    result = {"initialState": {"zonaltariffdescription": {"zoneName": "moscow", "isZoneUnsupported": False,
        "currentCategoryType": "application", "currency_rules": {"code": "RUB"}, "max_tariffs": clean}}}
    metadata = f"source={SOURCE} checked_utc={now.astimezone(dt.timezone.utc).isoformat()} sha256={hashlib.sha256(raw).hexdigest()}"
    # Retain the existing Android parser format without copying the page's executable code.
    return (f'<!-- {metadata} -->\n<p data-key="tariff.disclaimer.expiry.date">{dates[0]}.</p>\n'
            f'<script>__init__.default({json.dumps(result, ensure_ascii=True)});</script>\n').encode("utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected redirect")


def refresh(destination: Path) -> None:
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "AzimuthRadar/1.0", "Accept": "text/html"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("Unexpected HTTP status")
        raw = response.read(LIMIT + 1)
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != len(raw):
            raise ValueError("Truncated source")
    result = extract(raw, dt.datetime.now(dt.timezone.utc))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        temporary.write_bytes(result)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    refresh(Path(sys.argv[1]))
