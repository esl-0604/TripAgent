import json
from pathlib import Path

COUNTRY_TZ = {
    "일본": "Asia/Tokyo",
    "중국": "Asia/Shanghai",
    "대만": "Asia/Taipei",
    "홍콩": "Asia/Hong_Kong",
    "싱가포르": "Asia/Singapore",
    "태국": "Asia/Bangkok",
    "베트남": "Asia/Ho_Chi_Minh",
    "인도": "Asia/Kolkata",
    "UAE": "Asia/Dubai",
    "한국": "Asia/Seoul",
    "미국": "America/Los_Angeles",
    "캐나다": "America/Toronto",
    "멕시코": "America/Mexico_City",
    "브라질": "America/Sao_Paulo",
    "영국": "Europe/London",
    "프랑스": "Europe/Paris",
    "독일": "Europe/Berlin",
    "이탈리아": "Europe/Rome",
    "스페인": "Europe/Madrid",
    "네덜란드": "Europe/Amsterdam",
    "벨기에": "Europe/Brussels",
    "스위스": "Europe/Zurich",
    "오스트리아": "Europe/Vienna",
    "호주": "Australia/Sydney",
    "뉴질랜드": "Pacific/Auckland",
}

_OVERRIDE_PATH = Path(__file__).resolve().parent / "trip_timezone_overrides.json"


def get_timezone(country: str, trip_title: str = "") -> str:
    """Return IANA tz for given country, or per-trip override if set."""
    if _OVERRIDE_PATH.exists():
        try:
            overrides = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if trip_title and trip_title in overrides:
                return overrides[trip_title]
        except Exception:
            pass
    country_key = country.strip()
    if country_key not in COUNTRY_TZ:
        raise KeyError(
            f"Unknown country: {country_key!r}. Add to COUNTRY_TZ in trip_timezones.py "
            f"or override via {_OVERRIDE_PATH.name}"
        )
    return COUNTRY_TZ[country_key]
