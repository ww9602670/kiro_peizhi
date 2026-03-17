""""""
from __future__ import annotations

PLATFORM_CONFIGS: dict[str, dict] = {
    "JND28WEB": {
        "base_url": "https://166test.com",
        "lottery_type": "JND28WEB",
        "template_code": "JNDPCDD",
        "downtime_ranges": [
            ("19:56", "20:33"),
            ("06:00", "07:00"),
        ],
        "refund_rules": {},  # 
    },
    "JND282": {
        "base_url": "https://166test.com",
        "lottery_type": "JND282",
        "template_code": "JNDPCDD",
        "downtime_ranges": [
            ("19:56", "20:33"),
            ("06:00", "07:00"),
        ],
        "refund_rules": {
            14: {"DX1", "DS4", "ZH8"},   # 14
            13: {"DX2", "DS3", "ZH9"},   # 13
        },
    },
}


# 
MID_CODES = "HZ,DX,DS,ZH,SB,TMBS,JDX,BZ,B1QH,B1LM,B2QH,B2LM,B3QH,B3LM,LHH"

# 
DEFAULT_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
