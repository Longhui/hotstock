"""
公司名称查询
离线缓存常见 ticker + 在线 yfinance 回填
"""

from functools import lru_cache
import yfinance as yf
import logging

logger = logging.getLogger(__name__)

# 常见美股名称缓存（避免每次请求网络）
KNOWN_NAMES = {
    # 指数 ETF
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust (Nasdaq)",
    "DIA": "SPDR Dow Jones Industrial Average ETF",
    "IWM": "iShares Russell 2000 ETF",
    "VTI": "Vanguard Total Stock Market ETF",
    "VOO": "Vanguard S&P 500 ETF",
    "BND": "Vanguard Total Bond Market ETF",
    # 科技
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "GOOGL": "Alphabet Inc. (Class A)",
    "GOOG": "Alphabet Inc. (Class C)",
    "AMZN": "Amazon.com Inc.",
    "NVDA": "NVIDIA Corporation",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    "AMD": "Advanced Micro Devices Inc.",
    "INTC": "Intel Corporation",
    "NFLX": "Netflix Inc.",
    "ADBE": "Adobe Inc.",
    "CRM": "Salesforce Inc.",
    "ORCL": "Oracle Corporation",
    "IBM": "International Business Machines",
    "CSCO": "Cisco Systems Inc.",
    "QCOM": "Qualcomm Inc.",
    "TXN": "Texas Instruments Inc.",
    "AVGO": "Broadcom Inc.",
    "AMAT": "Applied Materials Inc.",
    "MU": "Micron Technology Inc.",
    "ASML": "ASML Holding N.V.",
    "TSMC": "Taiwan Semiconductor (TSMC)",
    "ARM": "Arm Holdings plc",
    # 互联网/电商
    "SE": "Sea Limited",
    "BABA": "Alibaba Group",
    "PDD": "PDD Holdings Inc.",
    "JD": "JD.com Inc.",
    "MELI": "MercadoLibre Inc.",
    "SHOP": "Shopify Inc.",
    "SNAP": "Snap Inc.",
    "PINS": "Pinterest Inc.",
    "PYPL": "PayPal Holdings Inc.",
    "SQ": "Block Inc. (Square)",
    "UBER": "Uber Technologies Inc.",
    "LYFT": "Lyft Inc.",
    "ABNB": "Airbnb Inc.",
    "DASH": "DoorDash Inc.",
    # 半导体
    "WDC": "Western Digital Corporation",
    "STX": "Seagate Technology Holdings",
    "MRVL": "Marvell Technology Inc.",
    "KLAC": "KLA Corporation",
    "LRCX": "Lam Research Corporation",
    "MCHP": "Microchip Technology Inc.",
    # 金融
    "JPM": "JPMorgan Chase & Co.",
    "BAC": "Bank of America Corp",
    "GS": "Goldman Sachs Group Inc.",
    "MS": "Morgan Stanley",
    "V": "Visa Inc.",
    "MA": "Mastercard Inc.",
    "AXP": "American Express Company",
    "BLK": "BlackRock Inc.",
    "SCHW": "Charles Schwab Corporation",
    "COIN": "Coinbase Global Inc.",
    "PLTR": "Palantir Technologies Inc.",
    "SOFI": "SoFi Technologies Inc.",
    # 消费
    "WMT": "Walmart Inc.",
    "COST": "Costco Wholesale Corp",
    "HD": "The Home Depot Inc.",
    "MCD": "McDonald's Corporation",
    "SBUX": "Starbucks Corporation",
    "NKE": "Nike Inc.",
    "DIS": "The Walt Disney Company",
    "AMC": "AMC Entertainment Holdings",
    "GME": "GameStop Corporation",
    # 医疗/生物科技
    "UNH": "UnitedHealth Group Inc.",
    "JNJ": "Johnson & Johnson",
    "PFE": "Pfizer Inc.",
    "MRK": "Merck & Co. Inc.",
    "ABBV": "AbbVie Inc.",
    "LLY": "Eli Lilly and Company",
    "VRTX": "Vertex Pharmaceuticals Inc.",
    "BSX": "Boston Scientific Corporation",
    "ISRG": "Intuitive Surgical Inc.",
    "REGN": "Regeneron Pharmaceuticals",
    # 能源/工业
    "XOM": "Exxon Mobil Corporation",
    "CVX": "Chevron Corporation",
    "COP": "ConocoPhillips",
    "SLB": "Schlumberger Limited",
    "BA": "The Boeing Company",
    "CAT": "Caterpillar Inc.",
    "GE": "General Electric Company",
    "HON": "Honeywell International Inc.",
    "UPS": "United Parcel Service Inc.",
    "FDX": "FedEx Corporation",
    # 电信/媒体
    "T": "AT&T Inc.",
    "VZ": "Verizon Communications Inc.",
    "CMCSA": "Comcast Corporation",
    "CHTR": "Charter Communications Inc.",
    "TMUS": "T-Mobile US Inc.",
    # 热门讨论股
    "RIVN": "Rivian Automotive Inc.",
    "LCID": "Lucid Group Inc.",
    "NIO": "NIO Inc.",
    "F": "Ford Motor Company",
    "GM": "General Motors Company",
    "PLTR": "Palantir Technologies Inc.",
    "IREN": "Iris Energy Limited",
    "SNDK": "SanDisk Corporation",
    "DD": "DuPont de Nemours Inc.",
    "BSX": "Boston Scientific Corporation",
    "PYPL": "PayPal Holdings Inc.",
    "FCEL": "FuelCell Energy Inc.",
    "META": "Meta Platforms Inc.",
    "DTE": "DTE Energy Company",
    "LSE": "Laleham Health & Beauty (or London Stock Exchange)",
    "WEN": "The Wendy's Company",
    "OND": "Ondas Holdings Inc.",
    "ONDS": "Ondas Holdings Inc.",
    "SLS": "SELLAS Life Sciences Group",
    "SEER": "Seer Inc.",
    "LUCY": "Innovative Eyewear Inc.",
    "OFIX": "Orthofix Medical Inc.",
    "NAV": "Navistar International (or Navient)",
    "BSX": "Boston Scientific Corporation",
    "FCEL": "FuelCell Energy Inc.",
    "META": "Meta Platforms Inc.",
    "COMP": "Compass Inc.",
    "NAV": "Navient Corporation",
    "GE": "General Electric Company",
    "LSE": "LSE: London Stock Exchange Group",
    "MGV": "Vanguard Mega Cap Value ETF",
    "WATCH": "Watch: possibly generic term",
    "SNDK": "SanDisk Corporation",
    "DD": "DuPont de Nemours Inc.",
    # 新增热门
    "SNOW": "Snowflake Inc.",
    "DDOG": "Datadog Inc.",
    "CRWD": "CrowdStrike Holdings Inc.",
    "ZS": "Zscaler Inc.",
    "MRNA": "Moderna Inc.",
    "BNTX": "BioNTech SE",
    "ENPH": "Enphase Energy Inc.",
    "SEDG": "SolarEdge Technologies Inc.",
    "FANG": "Diamondback Energy Inc.",
    "MP": "MP Materials Corp.",
    "UPST": "Upstart Holdings Inc.",
    "AFRM": "Affirm Holdings Inc.",
    "HOOD": "Robinhood Markets Inc.",
    "CPNG": "Coupang Inc.",
    "RKLB": "Rocket Lab USA Inc.",
    "ASTS": "AST SpaceMobile Inc.",
}

# 黑名单：常见英文/金融缩写，不是股票
NON_STOCK_TICKERS = {
    "DR", "TL", "SK", "CS", "CT", "BS", "CP", "CG", "CC", "DC",
    "ED", "ET", "EX", "EZ", "FC", "FF", "FI", "FL", "FN", "FO",
    "FW", "FX", "FY", "GA", "GB", "GH", "GI", "GL", "GM", "GN",
    "GP", "GQ", "GR", "GT", "GU", "GW", "GY", "HC", "HH", "HL",
    "HM", "HN", "HP", "HQ", "HR", "HT", "HU", "HW", "HX", "HY",
    "ID", "IE", "IG", "IJ", "IL", "IM", "IQ", "IR", "IS", "IT",
    "IU", "IV", "IW", "IX", "IZ", "JA", "JC", "JD", "JF", "JG",
    "JH", "JJ", "JK", "JL", "JM", "JN", "JO", "JP", "JR", "JS",
    "JT", "JU", "JV", "JW", "JX", "JY", "KD", "KF", "KG", "KH",
    "KJ", "KK", "KM", "KN", "KO", "KP", "KQ", "KR", "KS", "KT",
    "KU", "KV", "KW", "KX", "KY", "KZ", "LA", "LB", "LC", "LD",
    "LF", "LG", "LH", "LJ", "LK", "LL", "LM", "LN", "LO", "LP",
    "LQ", "LR", "LS", "LT", "LU", "LV", "LW", "LX", "LY", "LZ",
    "MA", "MB", "MC", "MD", "MF", "MG", "MH", "MJ", "MK", "ML",
    "MM", "MN", "MO", "MP", "MQ", "MR", "MT", "MV", "MW", "MX",
    "MY", "MZ", "NB", "NC", "ND", "NF", "NG", "NH", "NJ", "NK",
    "NL", "NM", "NN", "NO", "NP", "NQ", "NR", "NS", "NT", "NU",
    "NV", "NW", "NX", "NZ", "OA", "OB", "OC", "OD", "OE", "OF",
    "OG", "OH", "OJ", "OK", "OL", "OM", "ON", "OO", "OP", "OQ",
    "OR", "OS", "OT", "OU", "OV", "OW", "OX", "OY", "OZ",
    "PA", "PB", "PC", "PD", "PF", "PG", "PH", "PJ", "PK", "PL",
    "PM", "PN", "PO", "PP", "PQ", "PR", "PS", "PT", "PU", "PV",
    "PW", "PX", "PY", "PZ", "QA", "QB", "QC", "QD", "QE", "QF",
    "QG", "QH", "QI", "QJ", "QK", "QL", "QM", "QN", "QO", "QP",
    "QQ", "QR", "QS", "QT", "QU", "QV", "QW", "QX", "QY", "QZ",
    "RA", "RB", "RC", "RD", "RF", "RG", "RH", "RJ", "RK", "RL",
    "RM", "RN", "RO", "RP", "RQ", "RR", "RS", "RT", "RU", "RV",
    "RW", "RX", "RY", "RZ", "SA", "SB", "SC", "SD", "SF", "SG",
    "SH", "SJ", "SK", "SM", "SN", "SO", "SP", "SQ", "SR", "SS",
    "ST", "SU", "SV", "SW", "SX", "SY", "SZ", "TA", "TB", "TC",
    "TD", "TF", "TG", "TH", "TJ", "TK", "TM", "TN", "TO", "TP",
    "TQ", "TR", "TS", "TT", "TU", "TV", "TW", "TX", "TY", "TZ",
    "UA", "UB", "UC", "UD", "UE", "UF", "UG", "UH", "UJ", "UK",
    "UL", "UM", "UN", "UO", "UP", "UQ", "UR", "US", "UT", "UU",
    "UV", "UW", "UX", "UY", "UZ", "VA", "VB", "VC", "VD", "VE",
    "VF", "VG", "VH", "VJ", "VK", "VL", "VM", "VN", "VO", "VP",
    "VQ", "VR", "VS", "VT", "VU", "VV", "VW", "VX", "VY", "VZ",
    "WA", "WB", "WC", "WD", "WF", "WG", "WH", "WJ", "WK", "WL",
    "WM", "WN", "WO", "WP", "WQ", "WR", "WS", "WT", "WU", "WV",
    "WW", "WX", "WY", "WZ", "XA", "XB", "XC", "XD", "XE", "XF",
    "XG", "XH", "XJ", "XK", "XL", "XM", "XN", "XO", "XP", "XQ",
    "XR", "XS", "XT", "XU", "XV", "XW", "XX", "XY", "XZ",
    "YA", "YB", "YC", "YD", "YE", "YF", "YG", "YH", "YJ", "YK",
    "YL", "YM", "YN", "YO", "YP", "YQ", "YR", "YS", "YT", "YU",
    "YV", "YW", "YX", "YY", "YZ", "ZA", "ZB", "ZC", "ZD", "ZE",
    "ZF", "ZG", "ZH", "ZJ", "ZK", "ZL", "ZM", "ZN", "ZO", "ZP",
    "ZQ", "ZR", "ZS", "ZT", "ZU", "ZV", "ZW", "ZX", "ZY", "ZZ",
}


@lru_cache(maxsize=512)
def lookup(ticker: str) -> str:
    """查询公司名称，优先缓存 -> yfinance"""
    if ticker in KNOWN_NAMES:
        return KNOWN_NAMES[ticker]
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName") or info.get("symbol", "")
        if name and name != ticker:
            return name
    except Exception:
        pass
    return ""


def batch_lookup(tickers: list[str]) -> dict[str, str]:
    """批量查询，已缓存的立即返回，缺失的用 yfinance 补"""
    result = {}
    missing = []
    for t in tickers:
        if t in KNOWN_NAMES:
            result[t] = KNOWN_NAMES[t]
        else:
            missing.append(t)

    if missing:
        logger.info(f"  正在查询 {len(missing)} 个股票的公司名称...")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_lookup_single, t): t for t in missing}
            for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
                t = futures[future]
                try:
                    name = future.result(timeout=10)
                    result[t] = name
                except Exception:
                    result[t] = ""
                if i % 10 == 0:
                    logger.info(f"    已查询 {i}/{len(missing)}")

    return result


def _lookup_single(ticker: str) -> str:
    """单个 ticker 查询，超时 10 秒"""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName") or ""
        return name if name != ticker else ""
    except Exception:
        return ""
