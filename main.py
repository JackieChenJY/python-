from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import importlib.util
import math
from typing import Iterable, List, Dict, Tuple


WINDPY_AVAILABLE = importlib.util.find_spec("WindPy") is not None
if WINDPY_AVAILABLE:
    from WindPy import w


@dataclass(frozen=True)
class EtfBasicInfo:
    code: str
    name: str
    category: str


@dataclass(frozen=True)
class EtfMetrics:
    northbound_net_inflow: float
    main_net_inflow: float
    industry_ir: float
    turnover_quantile: float
    volatility_quantile: float
    beta_quantile: float


@dataclass(frozen=True)
class EtfRankingRow:
    info: EtfBasicInfo
    metrics: EtfMetrics
    score: float


def _quantile(sorted_values: List[float], value: float) -> float:
    if not sorted_values:
        return 0.0
    count = len(sorted_values)
    index = 0
    while index < count and sorted_values[index] <= value:
        index += 1
    return index / count


def _info_ratio(returns: Iterable[float], benchmark: Iterable[float]) -> float:
    excess = [r - b for r, b in zip(returns, benchmark)]
    if not excess:
        return 0.0
    mean_excess = sum(excess) / len(excess)
    variance = sum((x - mean_excess) ** 2 for x in excess) / len(excess)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return 0.0
    return mean_excess / std_dev


def _mock_wind_etf_list() -> List[EtfBasicInfo]:
    return [
        EtfBasicInfo("510300.SH", "沪深300ETF", "宽基"),
        EtfBasicInfo("510050.SH", "上证50ETF", "宽基"),
        EtfBasicInfo("512000.SH", "券商ETF", "行业"),
        EtfBasicInfo("512690.SH", "酒ETF", "行业"),
        EtfBasicInfo("516150.SH", "新能源ETF", "主题"),
        EtfBasicInfo("516160.SH", "科技ETF", "主题"),
    ]


def _mock_history() -> List[float]:
    base = [0.01 * math.sin(idx / 3.0) for idx in range(60)]
    return base


def _mock_metric(info: EtfBasicInfo) -> EtfMetrics:
    history = _mock_history()
    benchmark = [0.008 * math.cos(idx / 2.5) for idx in range(60)]
    turnover_series = sorted([abs(x) * 100 for x in history])
    volatility_series = sorted([abs(x) * 10 for x in history])
    beta_series = sorted([1 + x for x in history])
    current_turnover = turnover_series[len(turnover_series) // 2]
    current_volatility = volatility_series[len(volatility_series) // 3]
    current_beta = beta_series[len(beta_series) // 4]

    return EtfMetrics(
        northbound_net_inflow=round(12.5 + len(info.code), 2),
        main_net_inflow=round(8.7 + len(info.name), 2),
        industry_ir=round(_info_ratio(history, benchmark), 4),
        turnover_quantile=_quantile(turnover_series, current_turnover),
        volatility_quantile=_quantile(volatility_series, current_volatility),
        beta_quantile=_quantile(beta_series, current_beta),
    )


def fetch_etf_list(api_sequence: str) -> List[EtfBasicInfo]:
    if not WINDPY_AVAILABLE:
        return _mock_wind_etf_list()

    w.start()
    response = w.wset(
        "sectorconstituent",
        f"date={datetime.today():%Y-%m-%d};sectorid=a001010100000000",
        usedf=True,
    )
    if response.ErrorCode != 0:
        raise RuntimeError(f"Wind API error {response.ErrorCode}")

    data = response.Data
    codes = data["wind_code"].tolist()
    names = data["sec_name"].tolist()
    categories = ["未知" for _ in codes]
    return [EtfBasicInfo(code, name, category) for code, name, category in zip(codes, names, categories)]


def fetch_metrics(info: EtfBasicInfo, api_sequence: str) -> EtfMetrics:
    if not WINDPY_AVAILABLE:
        return _mock_metric(info)

    end = datetime.today()
    start = end - timedelta(days=365)
    northbound = w.wss(info.code, "shs_holding_netflow", f"tradeDate={end:%Y%m%d}")
    main_flow = w.wss(info.code, "fund_main_net_inflow", f"tradeDate={end:%Y%m%d}")
    returns = w.wsd(info.code, "pct_chg", start, end, "Fill=Previous")
    industry = w.wsd(info.code, "industry_pct_chg", start, end, "Fill=Previous")
    turnover = w.wsd(info.code, "turn", start, end, "Fill=Previous")
    volatility = w.wsd(info.code, "volatility", start, end, "Fill=Previous")
    beta = w.wsd(info.code, "beta", start, end, "Fill=Previous")

    history_returns = returns.Data[0]
    benchmark_returns = industry.Data[0]
    turnover_series = sorted(turnover.Data[0])
    volatility_series = sorted(volatility.Data[0])
    beta_series = sorted(beta.Data[0])
    current_turnover = turnover_series[-1]
    current_volatility = volatility_series[-1]
    current_beta = beta_series[-1]

    return EtfMetrics(
        northbound_net_inflow=northbound.Data[0][0],
        main_net_inflow=main_flow.Data[0][0],
        industry_ir=_info_ratio(history_returns, benchmark_returns),
        turnover_quantile=_quantile(turnover_series, current_turnover),
        volatility_quantile=_quantile(volatility_series, current_volatility),
        beta_quantile=_quantile(beta_series, current_beta),
    )


def rank_etfs(etfs: Iterable[EtfBasicInfo], api_sequence: str) -> Dict[str, List[EtfRankingRow]]:
    grouped: Dict[str, List[EtfRankingRow]] = {}
    for info in etfs:
        metrics = fetch_metrics(info, api_sequence)
        score = (
            metrics.northbound_net_inflow * 0.4
            + metrics.main_net_inflow * 0.3
            + metrics.industry_ir * 100 * 0.2
            + (metrics.turnover_quantile + metrics.volatility_quantile + metrics.beta_quantile) * 10 * 0.1
        )
        row = EtfRankingRow(info=info, metrics=metrics, score=score)
        grouped.setdefault(info.category, []).append(row)

    for category in grouped:
        grouped[category].sort(key=lambda item: item.score, reverse=True)
    return grouped


def display_rankings(rankings: Dict[str, List[EtfRankingRow]]) -> None:
    for category, rows in rankings.items():
        print(f"\n类别: {category}")
        print("排名 | 代码 | 名称 | 北向净流入 | 主力净流入 | 行业IR | 换手率分位 | 波动率分位 | Beta分位 | 得分")
        for idx, row in enumerate(rows, start=1):
            metrics = row.metrics
            print(
                f"{idx:>2} | {row.info.code} | {row.info.name} | "
                f"{metrics.northbound_net_inflow:.2f} | {metrics.main_net_inflow:.2f} | "
                f"{metrics.industry_ir:.4f} | {metrics.turnover_quantile:.2f} | "
                f"{metrics.volatility_quantile:.2f} | {metrics.beta_quantile:.2f} | {row.score:.2f}"
            )


def main() -> None:
    api_sequence = "ASSUMED_WIND_API_0001"
    etfs = fetch_etf_list(api_sequence)
    rankings = rank_etfs(etfs, api_sequence)
    display_rankings(rankings)


if __name__ == "__main__":
    main()
