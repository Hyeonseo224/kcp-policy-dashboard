from __future__ import annotations

from pathlib import Path
import csv
import io
import json
import math
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import numpy as np
from scipy.stats import linregress
from statsmodels.tsa.seasonal import STL

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"

KCP_API_MONTHLY = "https://api.korea-carbon-project.org/region/compare/monthly"
KCP_FROM = int(os.environ.get("KCP_FROM", "202001"))
KCP_TO = int(os.environ.get("KCP_TO", "202212"))

# 법정동 전체검색용 공개 정적 데이터.
# 해당 저장소는 code.go.kr 기반 법정동 데이터를 JSON/CSV로 제공했던 공개 저장소이다.
LEGAL_DONG_URL = (
    "https://raw.githubusercontent.com/kr-legal-dong/"
    "kr-legal-dong/refs/heads/main/dong.csv"
)
LEGAL_CACHE_SECONDS = 24 * 60 * 60

# KCP 개발자도구에서 확인된 metric code.
# en_fc_mc = 에너지 제조업 및 건설업
# agr_luu, wst_wiob는 KCP 화면에서 확인된 농업/폐기물 계열 코드로 연결.
INDICATORS = {
    "energy_manufacturing": {
        "label": "에너지 제조업 및 건설업 탄소 배출량",
        "metric": "en_fc_mc",
    },
    "agriculture": {
        "label": "농업 탄소 배출량",
        "metric": "agr_luu",
    },
    "waste": {
        "label": "폐기물 탄소 배출량",
        "metric": "wst_wiob",
    },
}

_legal_rows: list[dict] | None = None
_legal_loaded_at: float = 0.0


def json_request(url: str, payload: dict, timeout: int = 25):
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "KCP-Policy-Decision-Dashboard/1.0",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return response.status, json.loads(text)


def fetch_text(url: str, timeout: int = 25) -> str:
    req = Request(
        url,
        headers={"User-Agent": "KCP-Policy-Decision-Dashboard/1.0"},
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def load_legal_dongs(force: bool = False) -> list[dict]:
    global _legal_rows, _legal_loaded_at

    now = time.time()
    if (
        not force
        and _legal_rows is not None
        and now - _legal_loaded_at < LEGAL_CACHE_SECONDS
    ):
        return _legal_rows

    text = fetch_text(LEGAL_DONG_URL)
    reader = csv.DictReader(io.StringIO(text))
    rows = []

    for row in reader:
        active = str(row.get("active", "")).strip().lower() == "true"
        name = str(row.get("name", "")).strip()

        if not active:
            continue
        if not name.endswith(("동", "읍", "면")):
            continue

        code = str(row.get("code", "")).strip()
        si_code = str(row.get("siCode", "")).strip()
        gu_code = str(row.get("guCode", "")).strip()

        if len(code) < 8 or len(si_code) < 8 or len(gu_code) < 8:
            continue

        rows.append({
            "code": code,
            "siCode": si_code,
            "siName": str(row.get("siName", "")).strip(),
            "guCode": gu_code,
            "guName": str(row.get("guName", "")).strip(),
            "fullName": str(row.get("fullName", "")).strip(),
            "name": name,
            "kcpCodes": {
                "ctp_cd": int(si_code[:8]),
                "sig_cd": int(gu_code[:8]),
                "emd_cd": int(code[:8]),
            },
        })

    _legal_rows = rows
    _legal_loaded_at = now
    return rows


def normalize(value: str) -> str:
    return "".join(str(value or "").split()).lower()


def search_legal_dongs(query: str, limit: int = 12) -> list[dict]:
    wanted = normalize(query)
    rows = load_legal_dongs()

    matches = []
    for row in rows:
        name_n = normalize(row["name"])
        full_n = normalize(row["fullName"])

        if name_n == wanted:
            priority = 0
        elif name_n.startswith(wanted):
            priority = 1
        elif wanted in name_n:
            priority = 2
        elif wanted in full_n:
            priority = 3
        else:
            continue

        matches.append((priority, row["fullName"], row))

    matches.sort(key=lambda x: (x[0], x[1]))
    return [m[2] for m in matches[:limit]]


def find_legal_dong_by_code(code: str) -> dict | None:
    code = str(code).strip()
    for row in load_legal_dongs():
        if row["code"] == code:
            return row
    return None


def fetch_kcp_monthly(region: dict, indicator_key: str) -> dict:
    meta = INDICATORS.get(indicator_key)
    if not meta:
        raise ValueError("지원하지 않는 KCP 지표입니다.")

    payload = {
        "from": KCP_FROM,
        "to": KCP_TO,
        "metrics": [meta["metric"]],
        "regions": [region["kcpCodes"]],
    }

    status, response = json_request(KCP_API_MONTHLY, payload)

    if status not in (200, 201):
        raise RuntimeError(f"KCP API 응답 상태 {status}")

    if not isinstance(response, list) or not response:
        raise ValueError("KCP API에서 지역 월별 데이터가 반환되지 않았습니다.")

    # 요청한 지역 1곳이므로 첫 번째 결과를 사용한다.
    block = response[0]
    data = block.get("data") or []
    if not data:
        raise ValueError("KCP API에 해당 지역·지표의 월별 데이터가 없습니다.")

    monthly = []
    for item in data:
        ym = item.get("year_month")
        total = item.get("total")
        if ym is None or total is None:
            continue
        try:
            monthly.append((int(ym), float(total)))
        except (TypeError, ValueError):
            continue

    monthly.sort(key=lambda x: x[0])

    if len(monthly) < 24:
        raise ValueError(
            f"v3 계산에는 최소 24개월의 월별 데이터가 필요합니다. "
            f"KCP API에서 {len(monthly)}개월이 반환되었습니다."
        )

    return {
        "indicator": meta,
        "payload": payload,
        "monthly": monthly,
    }


def calculate_v3_metrics(monthly: list[tuple[int, float]]) -> dict:
    """탄소 v3의 feature 계산과 동일한 핵심 로직."""
    values = np.array([v for _, v in monthly], dtype=float)

    if len(values) < 24:
        raise ValueError("v3 지표 계산에는 최소 24개월의 데이터가 필요합니다.")

    # 최근 12개월 평균
    mean12 = float(np.mean(values[-12:]))

    # STL -> 최근 12개월 Trend -> 선형 기울기
    stl = STL(values, period=12, robust=True)
    stl_result = stl.fit()
    recent_trend = np.asarray(stl_result.trend[-12:], dtype=float)
    slope, _, _, _, _ = linregress(np.arange(12), recent_trend)

    # YoY = pandas pct_change(12) * 100 과 같은 정의.
    # v3처럼 NaN만 제외하고 최근 12개의 YoY를 사용한다.
    with np.errstate(divide="ignore", invalid="ignore"):
        yoy = (values[12:] / values[:-12] - 1.0) * 100.0

    yoy = yoy[~np.isnan(yoy)]
    recent_yoy = yoy[-12:]
    if len(recent_yoy) == 0:
        raise ValueError("YoY 계산 가능한 최근 12개월 자료가 없습니다.")

    repeat_increase = float(np.mean(recent_yoy > 0) * 100.0)

    latest_ym, latest_emission = monthly[-1]
    latest_ym_text = str(latest_ym)
    if len(latest_ym_text) == 6:
        latest_observation = (
            f"{latest_ym_text[:4]}년 {int(latest_ym_text[4:])}월"
        )
    else:
        latest_observation = latest_ym_text

    return {
        "latestEmission": float(latest_emission),
        "mean12": mean12,
        "trend": float(slope),
        "repeatIncrease": repeat_increase,
        "latestObservation": latest_observation,
        "monthCount": len(monthly),
    }


def kcp_lookup(code: str, indicator: str):
    region = find_legal_dong_by_code(code)
    if not region:
        return 404, {"ok": False, "message": "해당 법정동 코드를 찾지 못했습니다."}

    try:
        fetched = fetch_kcp_monthly(region, indicator)
        metrics = calculate_v3_metrics(fetched["monthly"])

        latest = metrics.pop("latestObservation")
        month_count = metrics.pop("monthCount")
        meta = fetched["indicator"]

        return 200, {
            "ok": True,
            "region": {
                "code": region["code"],
                "name": region["name"],
                "fullName": region["fullName"],
                "kcpCodes": region["kcpCodes"],
            },
            "indicator": indicator,
            "indicatorLabel": meta["label"],
            "kcpMetric": meta["metric"],
            "monthCount": month_count,
            "latestObservation": latest,
            "metrics": metrics,
            "source": KCP_API_MONTHLY,
            "period": {"from": KCP_FROM, "to": KCP_TO},
            "calculation": {
                "mean12": "최근 12개월 월별 배출량 평균",
                "trend": (
                    "STL(period=12, robust=True)로 추출한 trend의 "
                    "최근 12개월에 scipy.stats.linregress 적용"
                ),
                "repeatIncrease": (
                    "12개월 전 대비 YoY를 계산하고 최근 12개월 중 "
                    "YoY > 0인 월의 비율"
                ),
            },
        }

    except HTTPError as e:
        return 502, {
            "ok": False,
            "message": f"KCP API가 HTTP {e.code} 오류를 반환했습니다.",
        }
    except URLError as e:
        return 502, {
            "ok": False,
            "message": f"KCP API 연결에 실패했습니다: {e.reason}",
        }
    except ValueError as e:
        return 422, {"ok": False, "message": str(e)}
    except Exception as e:
        return 500, {
            "ok": False,
            "message": f"KCP 월별 데이터 처리 중 오류가 발생했습니다: {e}",
        }


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str | None = None):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return

        body = path.read_bytes()
        ctype = (
            content_type
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self._send_file(TEMPLATE_PATH, "text/html; charset=utf-8")
            return

        if parsed.path == "/api/health":
            self._send_json(200, {
                "ok": True,
                "service": "kcp-policy-dashboard",
                "kcpPeriod": {"from": KCP_FROM, "to": KCP_TO},
            })
            return

        if parsed.path == "/api/regions/search":
            params = parse_qs(parsed.query)
            query = (params.get("q", [""])[0] or "").strip()

            if not query:
                self._send_json(400, {
                    "ok": False,
                    "message": "검색할 동·읍·면 이름이 필요합니다.",
                })
                return

            try:
                results = search_legal_dongs(query)
                self._send_json(200, {
                    "ok": True,
                    "query": query,
                    "count": len(results),
                    "results": results,
                })
            except Exception as e:
                self._send_json(502, {
                    "ok": False,
                    "message": f"전국 법정동 코드 목록을 불러오지 못했습니다: {e}",
                })
            return

        if parsed.path == "/api/kcp/lookup":
            params = parse_qs(parsed.query)
            code = (params.get("code", [""])[0] or "").strip()
            indicator = params.get(
                "indicator", ["energy_manufacturing"]
            )[0]

            if not code:
                self._send_json(400, {
                    "ok": False,
                    "message": "법정동 코드가 필요합니다.",
                })
                return

            status, payload = kcp_lookup(code, indicator)
            self._send_json(status, payload)
            return

        self.send_error(404)

    def log_message(self, fmt, *args):
        print("[dashboard]", fmt % args)


def main():
    port = int(os.environ.get("PORT", "5000"))

    # Render 등 외부 호스팅에서 접근할 수 있도록 0.0.0.0에 바인딩.
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)

    print(f"KCP dashboard listening on 0.0.0.0:{port}")
    print(f"Local preview: http://127.0.0.1:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
