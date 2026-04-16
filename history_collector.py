"""历史开奖数据采集器 - 无 GUI 依赖，可被后端 API 或 CLI 直接调用。

用法：
    python history_collector.py --status
    python history_collector.py --fill-gap
    python history_collector.py --fill-days 60
    python history_collector.py --fill-range 2026-01-18 2026-03-18
    python history_collector.py --sync-to-backend
"""
from __future__ import annotations
import argparse, logging, os, sqlite3, time
from datetime import datetime, timedelta
from api_client import ApiClient

logger = logging.getLogger(__name__)
BASE_URL = "https://166test.com"
LOTTERY_TYPE = "JND28WEB"
REQUEST_TIMEOUT = 15
_HERE = os.path.dirname(os.path.abspath(__file__))
JND28_DB_PATH = os.environ.get("BOCAI_HISTORY_DB_PATH", os.path.join(_HERE, "jnd28.sqlite3"))
BACKEND_DB_PATH = os.environ.get("BOCAI_DB_PATH", os.path.join(_HERE, "backend", "data", "bocai.db"))

def jnd28_connect(db_path=None):
    path = db_path or JND28_DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS jnd28_history ("
                 "issue TEXT PRIMARY KEY, open_time TEXT,"
                 "d1 INTEGER, d2 INTEGER, d3 INTEGER, sum INTEGER, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_state (k TEXT PRIMARY KEY, v TEXT)")
    conn.commit()
    return conn

def upsert_result(conn, issue, open_time, result_csv):
    parts = [p.strip() for p in result_csv.split(",")]
    if len(parts) != 3:
        return False
    try:
        d1, d2, d3 = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return False
    conn.execute("INSERT INTO jnd28_history(issue,open_time,d1,d2,d3,sum,created_at) "
                 "VALUES(?,?,?,?,?,?,?) ON CONFLICT(issue) DO UPDATE SET "
                 "open_time=COALESCE(excluded.open_time,jnd28_history.open_time),"
                 "d1=excluded.d1,d2=excluded.d2,d3=excluded.d3,sum=excluded.sum",
                 (issue, open_time, d1, d2, d3, d1+d2+d3,
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return True

def get_data_status(conn):
    r = conn.execute("SELECT COUNT(*),MIN(CAST(issue AS INTEGER)),"
                     "MAX(CAST(issue AS INTEGER)),MIN(open_time),MAX(open_time) "
                     "FROM jnd28_history").fetchone()
    cnt, mn, mx, t0, t1 = r
    return {"total_count": cnt, "min_issue": str(mn) if mn else None,
            "max_issue": str(mx) if mx else None, "min_time": t0, "max_time": t1,
            "gap_count": (mx-mn+1-cnt) if mn and mx else 0}

def get_date_distribution(conn):
    return conn.execute("SELECT substr(open_time,1,10),COUNT(*) FROM jnd28_history "
                        "WHERE open_time IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()

class HistoryCollector:
    def __init__(self, db_path=None, base_url=BASE_URL, lottery_type=LOTTERY_TYPE):
        self.conn = jnd28_connect(db_path)
        self.api = ApiClient(base_url=base_url, lottery_type=lottery_type, timeout=REQUEST_TIMEOUT)
        self._tok = False
    def _ensure(self):
        if not self._tok:
            self.api.ensure_token(); self._tok = True
    def fill_date(self, ds):
        self._ensure()
        try:
            j = self.api.fetch_history(ds)
        except Exception:
            self._tok = False
            try:
                self._ensure(); j = self.api.fetch_history(ds)
            except Exception as e:
                logger.error("拉取 %s 失败: %s", ds, e); return 0
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        recs = (data or {}).get("Records") or []
        n = 0
        for r in recs:
            iss = str(r.get("Installments","")).strip()
            ot = str(r.get("LotteryTime", r.get("OpenTime",""))).strip()
            res = str(r.get("OpenResult","")).strip()
            if iss and res and upsert_result(self.conn, iss, ot or None, res):
                n += 1
        self.conn.commit()
        return n
    def fill_date_range(self, sd, ed, on_progress=None):
        s = datetime.strptime(sd, "%Y-%m-%d"); e = datetime.strptime(ed, "%Y-%m-%d")
        td = (e-s).days+1; tot = 0; fail = []
        for i in range(td):
            d = (s+timedelta(days=i)).strftime("%Y-%m-%d")
            c = self.fill_date(d)
            if c == 0: fail.append(d)
            tot += c
            logger.info("补全 %s: %d 条 (%d/%d)", d, c, i+1, td)
            if on_progress: on_progress(d, c, td, i)
            time.sleep(0.3)
        return {"total_inserted": tot, "days_processed": td, "days_failed": fail}
    def fill_last_n_days(self, days, on_progress=None):
        ed = datetime.now().strftime("%Y-%m-%d")
        sd = (datetime.now()-timedelta(days=days-1)).strftime("%Y-%m-%d")
        return self.fill_date_range(sd, ed, on_progress)
    def fill_gap_to_now(self, on_progress=None):
        st = get_data_status(self.conn)
        if st["max_time"]:
            sd = (datetime.strptime(st["max_time"][:10],"%Y-%m-%d")+timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            sd = (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")
        ed = datetime.now().strftime("%Y-%m-%d")
        if sd > ed: return {"total_inserted":0,"days_processed":0,"days_failed":[]}
        return self.fill_date_range(sd, ed, on_progress)
    def get_status(self): return get_data_status(self.conn)
    def close(self): self.conn.close()


# ---------------------------------------------------------------------------
# 8828 数据源补全（无需登录，直接 HTTP POST）
# ---------------------------------------------------------------------------
import re
try:
    import requests as _requests
except ImportError:
    _requests = None

_8828_API_URL = "https://8828355.com/index/get_history.html"
_8828_SYMBOL_MAP = {
    'e7ef8': 6, 'f3679': 0, 'f187f': 7,
    'ef798': 1, 'e36e0': 9, 'f0914': 3,
    'ff4e5': 8, 'e502f': 2, 'f9e58': 4, 'e4501': 5,
}

def _parse_8828_icon_font(html_str):
    """解析 8828 Icon Font 编码为 (d1, d2, d3) 或 None"""
    matches = re.findall(r'&#x([a-fA-F0-9]+);', html_str)
    if len(matches) != 3:
        return None
    results = []
    for m in matches:
        if m in _8828_SYMBOL_MAP:
            results.append(_8828_SYMBOL_MAP[m])
        else:
            return None
    return (results[0], results[1], results[2])

def _fetch_8828_date(date_str):
    """从 8828 获取指定日期的历史数据，返回 [(issue, open_time, d1, d2, d3, sum), ...]"""
    if _requests is None:
        raise ImportError("requests 库未安装")
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://8828355.com/history/jnd28.html',
        'Origin': 'https://8828355.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    resp = _requests.post(_8828_API_URL, data={'code': '130', 'limit': '500', 'date': date_str},
                          headers=headers, timeout=15)
    resp.raise_for_status()
    j = resp.json()
    if j.get('code') != 0:
        return []
    records = []
    for item in j.get('data', []):
        issue = str(item.get('number', '')).strip()
        open_time = str(item.get('time', '')).strip()
        data_str = str(item.get('data', '')).strip()
        if not issue or not data_str or '购买接口' in data_str:
            continue
        parsed = _parse_8828_icon_font(data_str)
        if parsed is None:
            continue
        d1, d2, d3 = parsed
        records.append((issue, open_time, d1, d2, d3, d1 + d2 + d3))
    return records


def fill_missing_from_8828(db_path=None, on_progress=None):
    """使用 8828 数据源补全 jnd28.sqlite3 中的缺失数据。

    策略：分析缺失期号所在的日期，逐日从 8828 拉取并 upsert。
    返回 {"total_inserted": int, "days_processed": int, "days_failed": list[str]}
    """
    conn = jnd28_connect(db_path)
    try:
        # 找出所有缺失段涉及的日期
        rows = conn.execute(
            "SELECT CAST(issue AS INTEGER) as iss, open_time FROM jnd28_history ORDER BY iss ASC"
        ).fetchall()
        if not rows:
            return {"total_inserted": 0, "days_processed": 0, "days_failed": []}

        missing_dates = set()
        for i in range(1, len(rows)):
            if rows[i][0] - rows[i-1][0] > 1:
                if rows[i-1][1]:
                    missing_dates.add(rows[i-1][1][:10])
                if rows[i][1]:
                    missing_dates.add(rows[i][1][:10])

        # 也补全到今天
        today = datetime.now().strftime("%Y-%m-%d")
        st = get_data_status(conn)
        if st["max_time"]:
            last_date = st["max_time"][:10]
            d = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
            while d.strftime("%Y-%m-%d") <= today:
                missing_dates.add(d.strftime("%Y-%m-%d"))
                d += timedelta(days=1)

        dates = sorted(missing_dates)
        if not dates:
            return {"total_inserted": 0, "days_processed": 0, "days_failed": []}

        total_inserted = 0
        days_failed = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 记录补全前的总数，用于计算真正新增的条数
        before_count = conn.execute("SELECT COUNT(*) FROM jnd28_history").fetchone()[0]

        for idx, ds in enumerate(dates):
            try:
                records = _fetch_8828_date(ds)
                n = 0
                for issue, ot, d1, d2, d3, s in records:
                    conn.execute(
                        "INSERT INTO jnd28_history(issue,open_time,d1,d2,d3,sum,created_at) "
                        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(issue) DO UPDATE SET "
                        "open_time=COALESCE(excluded.open_time,jnd28_history.open_time),"
                        "d1=excluded.d1,d2=excluded.d2,d3=excluded.d3,sum=excluded.sum",
                        (issue, ot or None, d1, d2, d3, s, now_str))
                    n += 1
                conn.commit()
                total_inserted += n
                logger.info("[%d/%d] %s: %d 条", idx + 1, len(dates), ds, n)
                if n == 0:
                    days_failed.append(ds)
            except Exception as e:
                logger.error("8828 补全 %s 失败: %s", ds, e)
                days_failed.append(ds)
            if on_progress:
                on_progress(ds, total_inserted, len(dates), idx)
            time.sleep(0.3)

        # 用实际新增数替代 upsert 计数
        after_count = conn.execute("SELECT COUNT(*) FROM jnd28_history").fetchone()[0]
        actual_new = after_count - before_count

        return {"total_inserted": actual_new, "days_processed": len(dates), "days_failed": days_failed}
    finally:
        conn.close()

def sync_jnd28_to_backend(jnd28_path=None, backend_path=None):
    src = jnd28_connect(jnd28_path)
    dp = backend_path or BACKEND_DB_PATH
    if not os.path.exists(dp):
        src.close(); raise FileNotFoundError(f"后端数据库不存在: {dp}")
    dst = sqlite3.connect(dp)
    dst.execute("PRAGMA journal_mode=WAL"); dst.execute("PRAGMA busy_timeout=5000")
    rows = src.execute("SELECT issue,open_time,d1,d2,d3,sum,created_at FROM jnd28_history "
                       "WHERE open_time IS NOT NULL ORDER BY CAST(issue AS INTEGER)").fetchall()
    sy = sk = 0
    for iss, ot, d1, d2, d3, s, ca in rows:
        c = dst.execute("INSERT OR IGNORE INTO lottery_results"
                        "(issue,open_result,sum_value,open_time,created_at) VALUES(?,?,?,?,?)",
                        (iss, f"{d1},{d2},{d3}", s, ot, ca or ot))
        if c.rowcount > 0: sy += 1
        else: sk += 1
    dst.commit(); dst.close(); src.close()
    return {"synced": sy, "skipped": sk, "total_source": len(rows)}

def _prg(d, c, t, i): print(f"  [{i+1}/{t}] {d}: {c} 条")

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description="JND28 历史数据采集器")
    p.add_argument("--fill-days", type=int, metavar="N", help="补全最近 N 天")
    p.add_argument("--fill-range", nargs=2, metavar=("START","END"), help="补全日期范围")
    p.add_argument("--fill-gap", action="store_true", help="自动补全缺口到今天")
    p.add_argument("--sync-to-backend", action="store_true", help="同步到后端 DB")
    p.add_argument("--status", action="store_true", help="显示数据概况")
    p.add_argument("--db", default=None, help="jnd28 数据库路径")
    a = p.parse_args()
    if a.status:
        conn = jnd28_connect(a.db); s = get_data_status(conn)
        print(f"总条数: {s['total_count']}\n期号范围: {s['min_issue']} ~ {s['max_issue']}")
        print(f"时间范围: {s['min_time']} ~ {s['max_time']}\n缺失期数: {s['gap_count']}")
        print("\n日期分布:")
        for d, c in get_date_distribution(conn): print(f"  {d}: {c} 条")
        conn.close(); return
    col = res = None
    if a.fill_days:
        col = HistoryCollector(db_path=a.db); res = col.fill_last_n_days(a.fill_days, _prg)
    elif a.fill_range:
        col = HistoryCollector(db_path=a.db); res = col.fill_date_range(a.fill_range[0], a.fill_range[1], _prg)
    elif a.fill_gap:
        col = HistoryCollector(db_path=a.db); res = col.fill_gap_to_now(_prg)
    elif a.sync_to_backend:
        r = sync_jnd28_to_backend(jnd28_path=a.db)
        print(f"同步完成: 写入 {r['synced']} 条, 跳过 {r['skipped']} 条, 源 {r['total_source']} 条"); return
    else:
        p.print_help(); return
    if res:
        print(f"\n完成: 写入 {res['total_inserted']} 条, 处理 {res['days_processed']} 天")
        if res["days_failed"]: print(f"失败天数: {res['days_failed']}")
    if col: col.close()

if __name__ == "__main__":
    main()
