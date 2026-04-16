import requests

class ApiClient:
    def __init__(self, base_url: str, lottery_type: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.lottery_type = lottery_type
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.base_url + "/",
            "Origin": self.base_url,
        })

    def ensure_token(self) -> str:
        # 关键：访客登录，服务端通过 Set-Cookie 下发 token
        r = self.s.get(self.base_url + "/Member/VisitorLogin", allow_redirects=False, timeout=self.timeout)
        # 跟随一次协议页（可选但建议完整走）
        if r.status_code == 302 and "Location" in r.headers:
            loc = r.headers["Location"]
            if loc.startswith("/"):
                self.s.get(self.base_url + loc, timeout=self.timeout)
        # 可选：进入 Home
        self.s.get(self.base_url + "/Home/Index", timeout=self.timeout)

        token = self.s.cookies.get("token")
        if not token:
            raise RuntimeError("VisitorLogin 后仍未获得 token")
        return token

    def _get_json(self, path: str, params: dict) -> dict:
        url = self.base_url + path
        r = self.s.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()

        ctype = (r.headers.get("content-type") or "").lower()
        if "application/json" not in ctype:
            # 常见：返回 HTML（被重定向/会话失效）
            raise RuntimeError(f"Non-JSON response: {r.text[:120]}")
        return r.json()

    def get_json_with_reauth(self, path: str, params: dict) -> dict:
        """
        先请求一次，如果 State=-2 或非JSON/异常，则自动 VisitorLogin 后重试一次。
        """
        try:
            j = self._get_json(path, params)
            if isinstance(j, dict) and j.get("State") == -2:
                # 会话超时
                self.ensure_token()
                j = self._get_json(path, params)
            return j
        except Exception:
            # 可能 HTML / 网络波动：尝试重新拿 token 再试一次
            self.ensure_token()
            return self._get_json(path, params)

    # 你用到的两个 API
    def fetch_realtime(self) -> dict:
        return self.get_json_with_reauth("/PlaceBet/GetCurrentInstall", {"lotteryType": self.lottery_type})

    def fetch_history(self, date_str: str) -> dict:
        return self.get_json_with_reauth("/ResultHistory/Lotteryresult", {"lotterytype": self.lottery_type, "lotteryTime": date_str})
