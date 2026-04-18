from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JNDDW3Profile:
    lottery_type: str
    odds_setting_code: str
    submit_mode: str = "single_request_required"
    live_status: str = "one_shot_gate_pending"


JND_DW3_PROFILES: dict[str, JNDDW3Profile] = {
    "JND28WEB": JNDDW3Profile(
        lottery_type="JND28WEB",
        odds_setting_code="DW3",
        live_status="one_shot_gate_pending",
    ),
    "JND282": JNDDW3Profile(
        lottery_type="JND282",
        odds_setting_code="DW3",
        live_status="one_shot_gate_pending",
    ),
}


def get_jnd_dw3_profile(lottery_type: str | None) -> JNDDW3Profile | None:
    if not lottery_type:
        return None
    return JND_DW3_PROFILES.get(str(lottery_type).upper())
