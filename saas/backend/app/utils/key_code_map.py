"""
KeyCode   + 

 PC28 


KeyCode  PLATFORM_API_REFERENCE.md
"""

# ---------------------------------------------------------------------------
# KeyCode  
# ---------------------------------------------------------------------------

KEY_CODE_MAP: dict[str, str] = {
    # 大小 (DX)
    "DX1": "大",
    "DX2": "小",
    # 单双 (DS)
    "DS3": "单",
    "DS4": "双",
    # 极值 (JDX)
    "JDX5": "极大",
    "JDX6": "极小",
    # 组合 (ZH)
    "ZH7": "大单",
    "ZH8": "大双",
    "ZH9": "小单",
    "ZH10": "小双",
    # 色波 (SB)
    "SB1": "红波",
    "SB2": "绿波",
    "SB3": "蓝波",
    # 豹子 (BZ)
    "BZ4": "豹子",
    # 龙虎和 (LHH)
    "LHH_L": "龙",
    "LHH_H": "虎",
    "LHH_HE": "和",
}

#  HZ1~HZ28  0~27
for _i in range(1, 29):
    KEY_CODE_MAP[f"HZ{_i}"] = f"和值{_i - 1}"

#  B{n}QH{d}  n{1,2,3}, d{0..9}
_BALL_NAMES = {1: "第一球", 2: "第二球", 3: "第三球"}
for _n in range(1, 4):
    for _d in range(10):
        KEY_CODE_MAP[f"B{_n}QH{_d}"] = f"{_BALL_NAMES[_n]}{_d}"

#  B{n}LM_{suffix}
_LM_NAMES = {"DA": "大", "X": "小", "D": "单", "S": "双"}
for _n in range(1, 4):
    for _suffix, _label in _LM_NAMES.items():
        KEY_CODE_MAP[f"B{_n}LM_{_suffix}"] = f"{_BALL_NAMES[_n]}{_label}"


def get_key_code_name(key_code: str) -> str:
    """ KeyCode  KeyCode """
    return KEY_CODE_MAP.get(key_code, key_code)


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

_RED_SET: set[int] = {3, 6, 9, 12, 15, 18, 21, 24}
_BLUE_SET: set[int] = {2, 5, 8, 11, 17, 20, 23, 26}
_GREEN_SET: set[int] = {1, 4, 7, 10, 16, 19, 22, 25}
#  0, 13, 14, 27 


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

def check_win(key_code: str, balls: list[int], sum_value: int) -> bool:
    """
     KeyCode 

    :
        key_code:   'DX1', 'HZ14'
        balls:      [ball1, ball2, ball3]  [0, 9]
        sum_value:  = balls[0] + balls[1] + balls[2]

    :
        True False 
         KeyCode  False
    """
    # ---  ---
    if key_code == "DX1":
        return sum_value >= 14
    if key_code == "DX2":
        return sum_value <= 13

    # ---  ---
    if key_code == "DS3":
        return sum_value % 2 == 1
    if key_code == "DS4":
        return sum_value % 2 == 0

    # ---  ---
    if key_code == "JDX5":
        return sum_value >= 22
    if key_code == "JDX6":
        return sum_value <= 5

    # ---  ---
    if key_code == "ZH7":
        return sum_value >= 14 and sum_value % 2 == 1  # 
    if key_code == "ZH8":
        return sum_value >= 14 and sum_value % 2 == 0  # 
    if key_code == "ZH9":
        return sum_value <= 13 and sum_value % 2 == 1  # 
    if key_code == "ZH10":
        return sum_value <= 13 and sum_value % 2 == 0  # 

    # ---  HZ1~HZ28  0~27 ---
    if key_code.startswith("HZ") and key_code[2:].isdigit():
        target = int(key_code[2:]) - 1  # HZ1  0
        return sum_value == target

    # ---  ---
    if key_code == "SB1":
        return sum_value in _RED_SET
    if key_code == "SB2":
        return sum_value in _GREEN_SET
    if key_code == "SB3":
        return sum_value in _BLUE_SET

    # ---  ---
    if key_code == "BZ4":
        return balls[0] == balls[1] == balls[2]

    # ---  B{n}QH{d} ---
    if "QH" in key_code and key_code[0] == "B" and key_code[1].isdigit():
        ball_idx = int(key_code[1]) - 1  # B10, B21, B32
        target_num = int(key_code.split("QH")[1])
        return balls[ball_idx] == target_num

    # ---  B{n}LM_{suffix} ---
    if "LM_" in key_code and key_code[0] == "B" and key_code[1].isdigit():
        ball_idx = int(key_code[1]) - 1
        suffix = key_code.split("LM_")[1]
        return _check_ball_lm(balls[ball_idx], suffix)

    # ---  ---
    if key_code == "LHH_L":
        return balls[0] > balls[2]  #  > 
    if key_code == "LHH_H":
        return balls[0] < balls[2]  #  < 
    if key_code == "LHH_HE":
        return balls[0] == balls[2]  #  == 

    #  KeyCode
    return False


def _check_ball_lm(ball_value: int, suffix: str) -> bool:
    """(5)/(<5)/()/()"""
    if suffix == "DA":
        return ball_value >= 5
    if suffix == "X":
        return ball_value < 5
    if suffix == "D":
        return ball_value % 2 == 1
    if suffix == "S":
        return ball_value % 2 == 0
    return False
