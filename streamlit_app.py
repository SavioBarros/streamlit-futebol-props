from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import local
from time import perf_counter
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from requests.adapters import HTTPAdapter, Retry

try:
    from rapidfuzz import fuzz, process

    HAS_RAPIDFUZZ = True
except Exception:
    HAS_RAPIDFUZZ = False


st.set_page_config(
    page_title="Football Props Cheat Sheet",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
:root{
  --bg:#0f1216; --bg-soft:#151922; --card:#10151d; --card-2:#0e141b;
  --stroke:#1e2532; --muted:#9aa4b2; --accent:#3ddc97; --over:#2ecc71; --under:#e74c3c;
}
html, body, .main { background: var(--bg); color:#e6edf3; }
section[data-testid="stSidebar"] { background: var(--bg-soft); border-right:1px solid var(--stroke); }
.block-container { padding-top:1.2rem; padding-bottom:2rem; }
div[data-testid="stMetric"]{
  background: linear-gradient(180deg, var(--card) 0%, var(--card-2) 100%);
  border:1px solid var(--stroke); border-radius:12px; padding:14px 16px;
}
.value-positive{ color: var(--over); font-weight:800; }
.value-negative{ color: var(--under); font-weight:800; }
hr{ border:none; border-top:1px solid var(--stroke); margin:18px 0; }
.small-muted{ color:var(--muted); font-size:12.5px; }
</style>
""",
    unsafe_allow_html=True,
)


BASE_URL = "https://api.the-odds-api.com/v4"
FD_BASE = "https://www.football-data.co.uk/mmz4284"
REQUEST_TIMEOUT = 20
MAX_ODDS_WORKERS = 6
THREAD_LOCAL = local()
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FootballPropsCheatSheet/1.0; +local)"
}

LEAGUES: dict[str, dict[str, str]] = {
    "soccer_epl": {
        "name": "Premier League",
        "div": "E0",
        "fbref": "9/shooting/Premier-League-Stats",
    },
    "soccer_spain_la_liga": {
        "name": "La Liga",
        "div": "SP1",
        "fbref": "12/shooting/La-Liga-Stats",
    },
    "soccer_germany_bundesliga": {
        "name": "Bundesliga",
        "div": "D1",
        "fbref": "20/shooting/Bundesliga-Stats",
    },
    "soccer_italy_serie_a": {
        "name": "Serie A",
        "div": "I1",
        "fbref": "11/shooting/Serie-A-Stats",
    },
    "soccer_france_ligue_one": {
        "name": "Ligue 1",
        "div": "F1",
        "fbref": "13/shooting/Ligue-1-Stats",
    },
    "soccer_netherlands_eredivisie": {
        "name": "Eredivisie",
        "div": "N1",
        "fbref": "23/shooting/Eredivisie-Stats",
    },
    "soccer_portugal_primeira_liga": {
        "name": "Primeira Liga",
        "div": "P1",
        "fbref": "32/shooting/Primeira-Liga-Stats",
    },
}

MATCH_MARKETS = "totals,alternate_totals_corners,alternate_totals_cards"
PLAYER_MARKETS = "player_shots_on_target"
MARKET_MAP = {
    "totals": "GOLS",
    "alternate_totals": "GOLS",
    "alternate_totals_corners": "ESC",
    "alternate_totals_cards": "CART",
    "player_shots_on_target": "SOT",
}
STAT_LABEL = {
    "GOLS": "Gols",
    "ESC": "Escanteios",
    "CART": "Cartões",
    "SOT": "Chutes ao gol",
}
LINE_ALLOW = {
    "GOLS": {0.5, 1.5, 2.5, 3.5, 4.5},
    "ESC": {x + 0.5 for x in range(6, 14)},
    "CART": {1.5, 2.5, 3.5, 4.5, 5.5, 6.5},
    "SOT": {0.5, 1.5, 2.5, 3.5},
}

TEAM_ALIASES = {
    "manchester united": "Man United",
    "manchester city": "Man City",
    "tottenham hotspur": "Tottenham",
    "tottenham": "Tottenham",
    "newcastle united": "Newcastle",
    "west ham united": "West Ham",
    "nottingham forest": "Nott'm Forest",
    "nottingham forest fc": "Nott'm Forest",
    "brighton and hove albion": "Brighton",
    "brighton & hove albion": "Brighton",
    "wolverhampton wanderers": "Wolves",
    "leicester city": "Leicester",
    "leeds united": "Leeds",
    "ipswich town": "Ipswich",
    "crystal palace": "Crystal Palace",
    "aston villa": "Aston Villa",
    "afc bournemouth": "Bournemouth",
    "atletico madrid": "Ath Madrid",
    "atlético madrid": "Ath Madrid",
    "atletico de madrid": "Ath Madrid",
    "athletic club": "Ath Bilbao",
    "athletic bilbao": "Ath Bilbao",
    "real betis": "Betis",
    "real sociedad": "Sociedad",
    "rayo vallecano": "Vallecano",
    "celta vigo": "Celta",
    "rc celta": "Celta",
    "deportivo alaves": "Alaves",
    "deportivo alavés": "Alaves",
    "bayern munich": "Bayern Munich",
    "fc bayern munich": "Bayern Munich",
    "borussia dortmund": "Dortmund",
    "bayer leverkusen": "Leverkusen",
    "rb leipzig": "Leipzig",
    "eintracht frankfurt": "Ein Frankfurt",
    "borussia monchengladbach": "M'gladbach",
    "borussia mönchengladbach": "M'gladbach",
    "tsg hoffenheim": "Hoffenheim",
    "werder bremen": "Werder Bremen",
    "1 fc koln": "FC Koln",
    "1. fc koln": "FC Koln",
    "1. fc köln": "FC Koln",
    "fc cologne": "FC Koln",
    "union berlin": "Union Berlin",
    "mainz 05": "Mainz",
    "fsv mainz 05": "Mainz",
    "inter milan": "Inter",
    "fc internazionale": "Inter",
    "internazionale": "Inter",
    "ac milan": "Milan",
    "as roma": "Roma",
    "ss lazio": "Lazio",
    "hellas verona": "Verona",
    "paris saint germain": "Paris SG",
    "paris saint-germain": "Paris SG",
    "psg": "Paris SG",
    "olympique marseille": "Marseille",
    "olympique lyonnais": "Lyon",
    "as monaco": "Monaco",
    "stade rennais": "Rennes",
    "rc lens": "Lens",
    "ogc nice": "Nice",
    "lille osc": "Lille",
    "sporting cp": "Sp Lisbon",
    "sporting lisbon": "Sp Lisbon",
    "fc porto": "Porto",
    "sl benfica": "Benfica",
    "ajax amsterdam": "Ajax",
    "psv eindhoven": "PSV Eindhoven",
    "feyenoord": "Feyenoord",
}

DEFAULT_FILTERS = {
    "leagues": ["soccer_epl"],
    "num_jogos": 10,
    "w_recent": 0.65,
    "bookmaker_sel": "Todos",
    "stat_sel": "Todas",
    "game_sel": "Todos",
    "side_sel": "Todos",
    "min_stars": 1,
    "odds_min": 1.55,
    "odds_max": 2.50,
    "min_value": 4,
    "min_cover_prob": 0,
    "top_per_game": 8,
    "static_plot": True,
    "player_search": "",
    "sort_by": "Value",
    "main_view": "Cheat Sheet",
}


def get_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets[name]
        return str(value).strip()
    except Exception:
        return os.getenv(name, default).strip()


API_KEY = get_secret("ODDS_API_KEY")


def european_season_codes(today: Optional[datetime] = None) -> tuple[str, str]:
    today = today or datetime.now(timezone.utc).replace(tzinfo=None)
    start = today.year if today.month >= 7 else today.year - 1
    current = f"{start % 100:02d}{(start + 1) % 100:02d}"
    previous = f"{(start - 1) % 100:02d}{start % 100:02d}"
    return current, previous


SEASON_CUR, SEASON_PREV = european_season_codes()


def _norm_text(text: str) -> str:
    value = str(text or "").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _safe_key(*parts: Any) -> str:
    raw = "_".join(str(p) for p in parts)
    return re.sub(r"\W+", "_", raw)


def build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HTTP_HEADERS)
    return session


def get_session() -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        THREAD_LOCAL.session = session
    return session


def parse_commence_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def format_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                if payload.get(key):
                    return str(payload[key])
    except Exception:
        pass
    body = (response.text or "").strip()
    return body[:220] if body else f"HTTP {response.status_code}"


def prob_from_decimal(odds: float) -> float:
    return 0.0 if not odds or odds <= 1e-9 else 1.0 / odds


def implied_percent(odds: float) -> float:
    return prob_from_decimal(odds) * 100.0


def no_vig_prob_over(odds_over: float, odds_under: float) -> float:
    po, pu = prob_from_decimal(odds_over), prob_from_decimal(odds_under)
    total = po + pu
    return (po / total * 100.0) if total > 0 else 50.0


def poisson_pmf(k: int, mu: float) -> float:
    if k < 0:
        return 0.0
    mu = max(1e-9, float(mu))
    return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))


def poisson_over_probability(line_value: float, mu: float) -> float:
    cap = int(math.floor(line_value))
    cdf = sum(poisson_pmf(k, mu) for k in range(cap + 1))
    return max(0.0, min(1.0, 1.0 - cdf))


def normal_over_probability(line_value: float, mu: float, sd: float) -> float:
    sd = max(1e-9, float(sd))
    z = (line_value - float(mu)) / sd
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return max(0.0, min(1.0, 1.0 - cdf))


def nbinom_pmf(k: int, r: float, p: float) -> float:
    """P(X=k) for a Negative Binomial with r failures / success-prob p (mean = r*(1-p)/p)."""
    if k < 0 or r <= 0 or not (0.0 < p < 1.0):
        return 0.0
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r) + r * math.log(p) + k * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


def nbinom_over_probability(line_value: float, mu: float, variance: float) -> float:
    """Over-probability for a count stat, using a Negative Binomial fit to (mu, variance).

    Counting stats like gols/cartões/escanteios/SOT are rarely pure Poisson in practice —
    real matches show overdispersion (variance > mean) driven by game state, referee
    tendencies, etc. The Negative Binomial nests Poisson as a limiting case (variance -> mu)
    but lets the *actual sample variance* (already computed by estimate_weighted_distribution)
    fatten the tails instead of being thrown away, which was happening before.
    """
    mu = max(1e-6, float(mu))
    variance = float(variance)
    cap = int(math.floor(line_value))
    if variance <= mu * 1.02:
        # Under/near-dispersed: NB is numerically unstable here, Poisson is the right limit.
        cdf = sum(poisson_pmf(k, mu) for k in range(cap + 1))
        return max(0.0, min(1.0, 1.0 - cdf))
    r = (mu * mu) / (variance - mu)
    p = r / (r + mu)
    cdf = sum(nbinom_pmf(k, r, p) for k in range(cap + 1))
    return max(0.0, min(1.0, 1.0 - cdf))


def over_probability(stat: str, line_value: float, mu: float, sd: float) -> float:
    # All four markets (gols, escanteios, cartões, chutes ao gol) are non-negative counts,
    # so all are modeled with the same Negative-Binomial/Poisson family rather than mixing in
    # a continuous Normal approximation for corners.
    return nbinom_over_probability(line_value, mu, sd * sd)


def estimate_weighted_distribution(
    recent: pd.Series,
    season: pd.Series,
    w_recent: float = 0.65,
    min_sd: float = 1.0,
) -> Optional[tuple[float, float]]:
    recent = pd.to_numeric(recent, errors="coerce").dropna()
    season = pd.to_numeric(season, errors="coerce").dropna()
    if recent.empty and season.empty:
        return None
    if len(recent) < 3:
        w_recent = min(w_recent, 0.35)
    if recent.empty:
        mu = float(season.mean())
        var = float(season.var(ddof=1)) if len(season) > 1 else min_sd**2
        return mu, math.sqrt(max(min_sd**2, var))
    if season.empty:
        mu = float(recent.mean())
        var = float(recent.var(ddof=1)) if len(recent) > 1 else min_sd**2
        return mu, math.sqrt(max(min_sd**2, var))
    mu_recent = float(recent.mean())
    mu_season = float(season.mean())
    var_recent = float(recent.var(ddof=1)) if len(recent) > 1 else min_sd**2
    var_season = float(season.var(ddof=1)) if len(season) > 1 else min_sd**2
    mu = w_recent * mu_recent + (1.0 - w_recent) * mu_season
    var_within = w_recent * var_recent + (1.0 - w_recent) * var_season
    var_between = w_recent * (mu_recent - mu) ** 2 + (1.0 - w_recent) * (mu_season - mu) ** 2
    sd = math.sqrt(max(min_sd**2, var_within + var_between))
    return mu, sd


def side_hit_mask(series: pd.Series, line_value: float, side: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric > line_value if side == "Over" else numeric <= line_value


def side_hit_rate(series: pd.Series, line_value: float, side: str) -> Optional[float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(side_hit_mask(numeric, line_value, side).mean() * 100.0)


def fmt_rate(rate: Optional[float]) -> str:
    return "—" if rate is None else f"{rate:.1f}%"


def compute_star_rating(value_pct: float, cover_prob_pct: float) -> int:
    edge = max(0.0, float(value_pct))
    cover = float(cover_prob_pct)
    score = edge * 1.35 + max(0.0, cover - 52.0) * 0.55
    if score >= 28:
        return 5
    if score >= 22:
        return 4
    if score >= 16:
        return 3
    if score >= 10:
        return 2
    return 1


def kelly_fraction(prob_pct: float, decimal_odds: float, fraction: float = 0.5) -> float:
    """Fractional Kelly stake (% of bankroll). Uses half-Kelly by default, which is the usual
    practical compromise: full Kelly is mathematically optimal for growth but assumes the
    model's probability is exactly right, and is brutally punishing (large drawdowns) when
    it isn't — which, for a heuristic sports model, it never is exactly.
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    p = max(0.0, min(1.0, prob_pct / 100.0))
    q = 1.0 - p
    full = (p * b - q) / b
    return max(0.0, full * fraction * 100.0)


def format_stars(count: int) -> str:
    count = max(1, min(5, int(count)))
    return "★" * count + "☆" * (5 - count)


def build_rec_id(*parts: Any) -> str:
    base = "|".join(str(p) for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:12]


def add_match_stat_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["FTHG", "FTAG", "HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR"]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["GOLS"] = out["FTHG"] + out["FTAG"]
    out["ESC"] = out["HC"] + out["AC"]
    out["CART"] = out["HY"].fillna(0) + out["AY"].fillna(0) + out["HR"].fillna(0) + out["AR"].fillna(0)
    out["SOT"] = out["HST"] + out["AST"]
    out["HOME_GOLS"] = out["FTHG"]
    out["AWAY_GOLS"] = out["FTAG"]
    out["HOME_ESC"] = out["HC"]
    out["AWAY_ESC"] = out["AC"]
    out["HOME_CART"] = out["HY"].fillna(0) + out["HR"].fillna(0)
    out["AWAY_CART"] = out["AY"].fillna(0) + out["AR"].fillna(0)
    out["HOME_SOT"] = out["HST"]
    out["AWAY_SOT"] = out["AST"]
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], dayfirst=True, errors="coerce")
    return out.dropna(subset=["HomeTeam", "AwayTeam"])


@st.cache_data(ttl=21_600, show_spinner=False)
def load_league_csv(div: str, season_code: str) -> pd.DataFrame:
    url = f"{FD_BASE}/{season_code}/{div}.csv"
    try:
        response = get_session().get(url, timeout=REQUEST_TIMEOUT)
        if not response.ok:
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(response.content.decode("latin-1", errors="ignore")))
        if df.empty:
            return pd.DataFrame()
        return add_match_stat_columns(df)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=21_600, show_spinner=False)
def load_league_history(div: str) -> pd.DataFrame:
    frames = [load_league_csv(div, SEASON_CUR), load_league_csv(div, SEASON_PREV)]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if "Date" in out.columns:
        out = out.sort_values("Date").reset_index(drop=True)
    return out


@st.cache_data(ttl=21_600, show_spinner=False)
def league_team_index(div: str) -> list[str]:
    history = load_league_history(div)
    if history.empty:
        return []
    names = pd.concat([history["HomeTeam"].astype(str), history["AwayTeam"].astype(str)], ignore_index=True)
    return sorted({name for name in names if name and name != "nan"})


def resolve_team_name(odds_name: str, div: str) -> Optional[str]:
    catalog = league_team_index(div)
    if not catalog:
        return None
    key = _norm_text(odds_name)
    alias = TEAM_ALIASES.get(key)
    if alias and alias in catalog:
        return alias
    index = {_norm_text(name): name for name in catalog}
    if key in index:
        return index[key]
    if HAS_RAPIDFUZZ and key:
        hit = process.extractOne(key, list(index.keys()), scorer=fuzz.WRatio, score_cutoff=80)
        if hit:
            return index[hit[0]]
        hit = process.extractOne(key, catalog, scorer=fuzz.WRatio, score_cutoff=80)
        if hit:
            return hit[0]
    return None


def team_matches(history: pd.DataFrame, team: str, venue: str = "all") -> pd.DataFrame:
    if history.empty or not team:
        return pd.DataFrame()
    if venue == "home":
        return history.loc[history["HomeTeam"] == team].copy()
    if venue == "away":
        return history.loc[history["AwayTeam"] == team].copy()
    return history.loc[(history["HomeTeam"] == team) | (history["AwayTeam"] == team)].copy()


def match_totals_for_team(history: pd.DataFrame, team: str, stat: str, venue: str = "all") -> pd.Series:
    games = team_matches(history, team, venue)
    if games.empty or stat not in games.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(games[stat], errors="coerce").dropna()


def side_stat_series(history: pd.DataFrame, team: str, stat: str, venue: str, role: str) -> pd.Series:
    games = team_matches(history, team, venue)
    if games.empty:
        return pd.Series(dtype=float)
    if venue == "home":
        col = f"HOME_{stat}" if role == "for" else f"AWAY_{stat}"
    else:
        col = f"AWAY_{stat}" if role == "for" else f"HOME_{stat}"
    if col not in games.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(games[col], errors="coerce").dropna()


def expected_match_total(
    history: pd.DataFrame,
    home: str,
    away: str,
    stat: str,
    num_jogos: int,
    w_recent: float,
) -> Optional[tuple[float, float, pd.Series, pd.Series]]:
    min_sd = {"GOLS": 0.85, "ESC": 2.2, "CART": 1.1, "SOT": 2.0}.get(stat, 1.0)
    home_for = side_stat_series(history, home, stat, "home", "for")
    home_against = side_stat_series(history, home, stat, "home", "against")
    away_for = side_stat_series(history, away, stat, "away", "for")
    away_against = side_stat_series(history, away, stat, "away", "against")
    if home_for.empty or away_for.empty:
        return None

    def blend(series: pd.Series) -> Optional[tuple[float, float]]:
        return estimate_weighted_distribution(series.tail(int(num_jogos)), series, w_recent=w_recent, min_sd=min_sd)

    parts = [blend(home_for), blend(home_against), blend(away_for), blend(away_against)]
    if any(item is None for item in parts):
        return None
    hf, ha, af, aa = parts  # type: ignore[misc]
    mu_home = 0.5 * (hf[0] + aa[0])
    mu_away = 0.5 * (af[0] + ha[0])
    mu = mu_home + mu_away
    var = 0.25 * (hf[1] ** 2 + aa[1] ** 2 + af[1] ** 2 + ha[1] ** 2)
    sd = math.sqrt(max(min_sd**2, var * 2.0))
    home_totals = match_totals_for_team(history, home, stat, "home")
    away_totals = match_totals_for_team(history, away, stat, "away")
    return mu, sd, home_totals, away_totals


def h2h_totals(history: pd.DataFrame, home: str, away: str, stat: str) -> pd.Series:
    if history.empty:
        return pd.Series(dtype=float)
    mask = ((history["HomeTeam"] == home) & (history["AwayTeam"] == away)) | (
        (history["HomeTeam"] == away) & (history["AwayTeam"] == home)
    )
    games = history.loc[mask]
    if games.empty or stat not in games.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(games[stat], errors="coerce").dropna()


FBREF_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _strip_fbref_comments(html_text: str) -> str:
    """FBref wraps most of its per-player stat tables inside HTML comments to make naive
    scraping harder. pandas.read_html() ignores commented-out markup entirely, so without
    this the 'shooting' table (which is exactly the one we need for SOT/90) is silently
    invisible and load_fbref_shooting() always returns empty — quietly killing the whole
    'chutes ao gol' market with no error shown anywhere. Un-wrapping the comments before
    handing the HTML to read_html fixes that.
    """
    return FBREF_COMMENT_RE.sub(lambda m: m.group(1), html_text)


@st.cache_data(ttl=43_200, show_spinner=False)
def load_fbref_shooting(fbref_path: str) -> pd.DataFrame:
    url = f"https://fbref.com/en/comps/{fbref_path}"
    try:
        response = get_session().get(url, timeout=REQUEST_TIMEOUT)
        if not response.ok:
            return pd.DataFrame()
        html_text = _strip_fbref_comments(response.text)
        tables = pd.read_html(io.StringIO(html_text))
    except Exception:
        return pd.DataFrame()
    for table in tables:
        cols = [" ".join(map(str, col)).strip() if isinstance(col, tuple) else str(col) for col in table.columns]
        table = table.copy()
        table.columns = cols
        player_col = next((c for c in table.columns if "player" in c.lower() and "rk" not in c.lower()), None)
        squad_col = next((c for c in table.columns if c.lower() in {"squad", "team"}), None)
        sot90_col = next((c for c in table.columns if "sot/90" in c.lower() or c.lower().endswith("sot/90")), None)
        sot_col = next((c for c in table.columns if c.lower() == "sot" or c.lower().endswith(" sot")), None)
        mp_col = next((c for c in table.columns if c.lower() in {"90s", "90s "} or "90s" in c.lower()), None)
        if player_col is None:
            continue
        out = pd.DataFrame(
            {
                "player": table[player_col].astype(str),
                "squad": table[squad_col].astype(str) if squad_col else pd.NA,
                "sot90": pd.to_numeric(table[sot90_col], errors="coerce") if sot90_col else pd.NA,
                "sot": pd.to_numeric(table[sot_col], errors="coerce") if sot_col else pd.NA,
                "nineties": pd.to_numeric(table[mp_col], errors="coerce") if mp_col else pd.NA,
            }
        )
        out = out[~out["player"].str.contains("player|squad", case=False, na=False)]
        if out["sot90"].isna().all() and out["sot"].notna().any() and out["nineties"].notna().any():
            out["sot90"] = out["sot"] / out["nineties"].replace(0, pd.NA)
        return out.dropna(subset=["player"])
    return pd.DataFrame()


def resolve_player_sot(player_name: str, fbref_path: str) -> Optional[tuple[float, Optional[str]]]:
    """Returns (sot90, squad_name_or_None) for the best-matched player, or None if no match."""
    table = load_fbref_shooting(fbref_path)
    if table.empty:
        return None
    key = _norm_text(player_name)
    rows = {
        _norm_text(name): (float(row.sot90), (str(row.squad) if pd.notna(row.squad) else None))
        for name, row in table.set_index("player").iterrows()
        if pd.notna(row.sot90)
    }
    if not rows:
        return None
    if key in rows:
        return rows[key]
    if HAS_RAPIDFUZZ:
        hit = process.extractOne(key, list(rows.keys()), scorer=fuzz.WRatio, score_cutoff=88)
        if hit:
            return rows[hit[0]]
    return None


def opponent_sot_adjustment(history: pd.DataFrame, opponent: Optional[str], opponent_venue: str) -> float:
    """Multiplier applied to a player's raw SoT/90 based on how many shots on target the
    opponent concedes (at that venue) relative to the league average. Clipped to a sane
    range so a handful of noisy matches can't blow the projection up or down too far.
    """
    if history.empty or not opponent:
        return 1.0
    league_conceded = pd.to_numeric(
        history["AWAY_SOT"] if opponent_venue == "home" else history["HOME_SOT"], errors="coerce"
    ).dropna()
    opp_conceded = side_stat_series(history, opponent, "SOT", opponent_venue, "against")
    if league_conceded.empty or opp_conceded.empty or len(opp_conceded) < 3:
        return 1.0
    league_avg = league_conceded.mean()
    if league_avg <= 0:
        return 1.0
    ratio = opp_conceded.tail(10).mean() / league_avg
    return max(0.75, min(1.30, ratio))


def fetch_events(sport_key: str, api_key: str) -> dict[str, Any]:
    url = f"{BASE_URL}/sports/{sport_key}/events"
    try:
        response = get_session().get(url, params={"apiKey": api_key}, timeout=REQUEST_TIMEOUT)
        if not response.ok:
            return {"events": [], "error": format_api_error(response), "status_code": response.status_code}
        data = response.json()
        events = data if isinstance(data, list) else []
        for event in events:
            event["sport_key"] = sport_key
            event["league_name"] = LEAGUES.get(sport_key, {}).get("name", sport_key)
        return {"events": events, "error": None, "status_code": response.status_code}
    except Exception as exc:
        return {"events": [], "error": str(exc), "status_code": None}


def fetch_event_odds(sport_key: str, event_id: str, api_key: str, markets: str, regions: str) -> dict[str, Any]:
    url = f"{BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "decimal",
    }
    try:
        response = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return {"payload": None, "error": "evento sem odds disponíveis", "status_code": 404}
        if not response.ok:
            return {"payload": None, "error": format_api_error(response), "status_code": response.status_code}
        return {"payload": response.json(), "error": None, "status_code": response.status_code}
    except Exception as exc:
        return {"payload": None, "error": str(exc), "status_code": None}


def extract_props_from_event(event_row: dict[str, Any], odds_payload: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not odds_payload or "bookmakers" not in odds_payload:
        return []
    event_id = event_row.get("id")
    home = event_row.get("home_team") or ""
    away = event_row.get("away_team") or ""
    sport_key = event_row.get("sport_key") or ""
    league_name = event_row.get("league_name") or sport_key
    commence = parse_commence_time(event_row.get("commence_time") or "")
    props: list[dict[str, Any]] = []

    for bookmaker in odds_payload.get("bookmakers", []):
        book_name = bookmaker.get("title") or bookmaker.get("key") or "book"
        for market in bookmaker.get("markets", []):
            stat = MARKET_MAP.get(market.get("key"))
            if not stat:
                continue
            bucket: dict[tuple[str, float], dict[str, float]] = {}
            for outcome in market.get("outcomes", []):
                line_value = outcome.get("point")
                side = str(outcome.get("name") or "").lower()
                price = outcome.get("price")
                if line_value is None or price is None:
                    continue
                if stat == "SOT":
                    subject = outcome.get("description") or ""
                    kind = "player"
                else:
                    subject = f"{away} vs {home}"
                    kind = "match"
                if not subject:
                    continue
                key = (subject, float(line_value))
                bucket.setdefault(key, {})
                if "over" in side:
                    bucket[key]["over"] = float(price)
                elif "under" in side:
                    bucket[key]["under"] = float(price)

            allowed = LINE_ALLOW.get(stat)
            for (subject, line_value), prices in bucket.items():
                if allowed and line_value not in allowed:
                    continue
                if "over" not in prices or "under" not in prices:
                    continue
                props.append(
                    {
                        "event_id": event_id,
                        "sport_key": sport_key,
                        "league_name": league_name,
                        "subject": subject,
                        "kind": "player" if stat == "SOT" else "match",
                        "stat": stat,
                        "line": float(line_value),
                        "odds_over": prices["over"],
                        "odds_under": prices["under"],
                        "bookmaker": book_name,
                        "game": f"{away} vs {home}",
                        "away_team": away,
                        "home_team": home,
                        "commence": commence,
                        "prob_book_fair_over": no_vig_prob_over(prices["over"], prices["under"]),
                    }
                )
    return props


@st.cache_data(ttl=300, show_spinner=False)
def get_football_props(api_key: str, league_keys: tuple[str, ...]) -> dict[str, Any]:
    all_events: list[dict[str, Any]] = []
    errors: list[str] = []
    for sport_key in league_keys:
        result = fetch_events(sport_key, api_key)
        if result.get("error"):
            errors.append(f"{LEAGUES.get(sport_key, {}).get('name', sport_key)}: {result['error']}")
        all_events.extend(result.get("events", []))

    if not all_events:
        return {
            "props": [],
            "error": errors[0] if errors else "nenhum evento retornado pela Odds API",
            "events_total": 0,
            "events_considered": 0,
            "event_errors": errors,
        }

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now_utc + timedelta(hours=48)
    valid_events = [
        event
        for event in all_events
        if event.get("id") and ((parse_commence_time(event.get("commence_time")) or now_utc) <= horizon)
    ]
    valid_events = sorted(valid_events, key=lambda item: parse_commence_time(item.get("commence_time")) or now_utc)
    if not valid_events:
        valid_events = [event for event in all_events if event.get("id")][:8]
    else:
        valid_events = valid_events[:12]

    collected: list[dict[str, Any]] = []
    event_errors: list[str] = errors[:]
    workers = min(MAX_ODDS_WORKERS, max(1, len(valid_events)))

    def pull(event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        sport_key = str(event.get("sport_key") or "")
        event_id = str(event["id"])
        match_result = fetch_event_odds(sport_key, event_id, api_key, MATCH_MARKETS, "uk,eu")
        player_result = fetch_event_odds(sport_key, event_id, api_key, PLAYER_MARKETS, "us")
        rows: list[dict[str, Any]] = []
        rows.extend(extract_props_from_event(event, match_result.get("payload")))
        rows.extend(extract_props_from_event(event, player_result.get("payload")))
        err = match_result.get("error") or player_result.get("error")
        return {"event": event, "error": err}, rows

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(pull, event) for event in valid_events]
        for future in as_completed(futures):
            meta, rows = future.result()
            event = meta["event"]
            if meta.get("error") and len(event_errors) < 8:
                matchup = f"{event.get('away_team', '?')} vs {event.get('home_team', '?')}"
                event_errors.append(f"{matchup}: {meta['error']}")
            collected.extend(rows)

    error = None
    if not collected:
        error = event_errors[0] if event_errors else "nenhuma linha disponível para os jogos consultados"
    return {
        "props": collected,
        "error": error,
        "events_total": len(all_events),
        "events_considered": len(valid_events),
        "event_errors": event_errors,
    }


def dedupe_props(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Line-shop for the best executable price on each side (that part is correct — it's the
    # price you could actually take). But the "fair" no-vig probability must NOT be
    # recomputed from that best-of-both-books pair: mixing the best over price from Book A
    # with the best under price from Book B silently removes real vig and manufactures an
    # artificially "fair" probability that no single book actually offers. Instead we keep
    # each incoming prop's own single-book fair value (already computed in
    # extract_props_from_event from that book's own two-sided price) and average those —
    # a genuine cross-book *consensus*, not a synthetic arbitrage line.
    best: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    fair_values: dict[tuple[str, str, float, str], list[float]] = {}
    for prop in props:
        key = (prop["subject"], prop["stat"], float(prop["line"]), prop["game"])
        fair_values.setdefault(key, []).append(float(prop["prob_book_fair_over"]))
        if key not in best:
            best[key] = {
                **prop,
                "bookmaker_over": prop["bookmaker"],
                "bookmaker_under": prop["bookmaker"],
            }
            continue
        chosen = best[key]
        if float(prop["odds_over"]) > float(chosen["odds_over"]):
            chosen["odds_over"] = float(prop["odds_over"])
            chosen["bookmaker_over"] = prop["bookmaker"]
        if float(prop["odds_under"]) > float(chosen["odds_under"]):
            chosen["odds_under"] = float(prop["odds_under"])
            chosen["bookmaker_under"] = prop["bookmaker"]
    for key, chosen in best.items():
        values = fair_values.get(key) or [chosen["prob_book_fair_over"]]
        chosen["prob_book_fair_over"] = sum(values) / len(values)
    return list(best.values())


@st.cache_data(show_spinner=False)
def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


@st.cache_data(show_spinner=False)
def records_to_json(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, ensure_ascii=False, indent=2, default=str)


def render_series_chart(
    series_df: pd.DataFrame,
    stat: str,
    line_value: float,
    title: str,
    key: str,
    static_plot: bool,
) -> None:
    if series_df.empty:
        st.info(f"Sem jogos para {title}")
        return
    values = pd.to_numeric(series_df[stat], errors="coerce").dropna()
    if values.empty:
        st.info(f"Sem dados de {STAT_LABEL.get(stat, stat)} em {title}")
        return
    labels = []
    for _, row in series_df.iterrows():
        date_value = row.get("Date")
        date_str = pd.to_datetime(date_value).strftime("%d/%m") if pd.notna(date_value) else "--/--"
        labels.append(f"{row.get('HomeTeam', '')} {int(row.get(stat, 0)) if pd.notna(row.get(stat)) else '-'} {row.get('AwayTeam', '')}\n{date_str}")
    y_values = pd.to_numeric(series_df[stat], errors="coerce").fillna(0).tolist()
    colors = ["#2ecc71" if value > line_value else "#e74c3c" for value in y_values]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=y_values,
            marker_color=colors,
            text=[f"{value:.0f}" for value in y_values],
            textposition="outside",
        )
    )
    fig.add_hline(y=line_value, line_dash="dash", line_color="#FFA500", annotation_text=f"Linha {line_value:g}")
    fig.update_layout(
        title=title,
        height=360,
        margin=dict(l=10, r=10, t=40, b=80),
        xaxis_tickangle=-30,
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "staticPlot": static_plot}, key=key)
    hits = int((values > line_value).sum())
    st.caption(f"Over {line_value:g}: {hits}/{len(values)} ({hits / len(values) * 100:.1f}%) · média {values.mean():.2f}")


@st.fragment
def render_selected_detail(detail: dict[str, Any], static_plot: bool) -> None:
    st.markdown("<hr/>", unsafe_allow_html=True)
    col1, col2 = st.columns([5, 1])
    with col1:
        st.subheader(
            f"🔎 {detail['Mercado']} — {STAT_LABEL.get(detail['Stat'], detail['Stat'])} {detail['Lado']} {detail['Linha']}"
        )
        st.caption(
            f"{detail['Liga']} · {detail['Jogo']} · {detail['Melhor Book']} @ {detail['Odds']:.2f} · "
            f"projeção {detail['Projeção']:.2f} · value {detail['Value (modelo) %']:.1f}%"
        )
    with col2:
        if st.button("Fechar", key=f"close_{detail['rec_id']}", width="stretch"):
            st.session_state["selected_rec_id"] = None
            st.rerun()

    if detail["kind"] == "player":
        st.info(
            "Chute ao gol é linha de **jogador**. A projeção usa SoT/90 do FBref "
            f"({detail['Projeção']:.2f} por 90 min). Sem game log individual no CSV de clubes."
        )
        return

    div = LEAGUES.get(detail["sport_key"], {}).get("div")
    history = load_league_history(div) if div else pd.DataFrame()
    home = detail["home_fd"]
    away = detail["away_fd"]
    stat = detail["Stat"]
    if history.empty or not home or not away:
        st.warning("Sem histórico de clubes para este jogo.")
        return

    home_games = team_matches(history, home, "home").tail(12)
    away_games = team_matches(history, away, "away").tail(12)
    h2h_mask = ((history["HomeTeam"] == home) & (history["AwayTeam"] == away)) | (
        (history["HomeTeam"] == away) & (history["AwayTeam"] == home)
    )
    h2h_games = history.loc[h2h_mask].tail(10)
    tabs = st.tabs(["🏠 Mandante (casa)", "✈️ Visitante (fora)", "🆚 H2H"])
    with tabs[0]:
        render_series_chart(home_games, stat, float(detail["Linha"]), f"Totais em casa — {home}", f"h_{detail['rec_id']}", static_plot)
    with tabs[1]:
        render_series_chart(away_games, stat, float(detail["Linha"]), f"Totais fora — {away}", f"a_{detail['rec_id']}", static_plot)
    with tabs[2]:
        render_series_chart(h2h_games, stat, float(detail["Linha"]), "Confrontos diretos", f"x_{detail['rec_id']}", static_plot)


slate_date = datetime.now(timezone.utc).strftime("%d/%m/%Y")
st.title("⚽ Football Prop Cheat Sheet")
st.subheader(f"Escanteios · Gols · Cartões · Chutes ao gol — {slate_date}")
st.caption(
    f"Temporadas {SEASON_PREV} + {SEASON_CUR} (football-data.co.uk) · "
    "linhas The Odds API · modelo ataca/defende ponderado pela forma recente."
)
header_left, header_right = st.columns([5, 1])
with header_right:
    if st.button("🔄 Atualizar", width="stretch"):
        st.cache_data.clear()
        st.session_state["selected_rec_id"] = None
        st.rerun()

if not API_KEY:
    st.error("Configure `ODDS_API_KEY` em `.streamlit/secrets.toml` ou na variável de ambiente.")
    st.stop()

for key, value in DEFAULT_FILTERS.items():
    st.session_state.setdefault(key, value)

st.sidebar.title("⚙️ Configurações")
st.sidebar.caption("Clique em **Aplicar filtros** para recalcular.")

league_keys = list(LEAGUES.keys())
league_labels = {key: meta["name"] for key, meta in LEAGUES.items()}

with st.sidebar.form("filters_form"):
    leagues_form = st.multiselect(
        "🏆 Ligas",
        league_keys,
        default=[key for key in st.session_state["leagues"] if key in league_keys] or ["soccer_epl"],
        format_func=lambda key: league_labels.get(key, key),
    )
    num_jogos_form = st.slider("📊 Jogos recentes (ponderação)", 5, 20, int(st.session_state["num_jogos"]))
    w_recent_form = st.slider("⚖️ Peso forma recente", 0.0, 1.0, float(st.session_state["w_recent"]), 0.05)
    st.markdown("---")
    stat_sel_form = st.selectbox(
        "📊 Mercado",
        ["Todas", "GOLS", "ESC", "CART", "SOT"],
        index=["Todas", "GOLS", "ESC", "CART", "SOT"].index(st.session_state["stat_sel"]),
        format_func=lambda item: "Todas" if item == "Todas" else STAT_LABEL.get(item, item),
    )
    side_sel_form = st.selectbox(
        "↕️ Lado",
        ["Todos", "Over", "Under"],
        index=["Todos", "Over", "Under"].index(st.session_state["side_sel"]),
    )
    min_stars_form = st.slider("⭐ Estrelas mínimas", 1, 5, int(st.session_state["min_stars"]))
    player_search_form = st.text_input("🔎 Time ou jogador", value=st.session_state["player_search"])
    sort_by_form = st.selectbox(
        "↕️ Ordenar por",
        ["Value", "Probabilidade", "Odds"],
        index=["Value", "Probabilidade", "Odds"].index(st.session_state["sort_by"]),
    )
    st.markdown("---")
    odds_min_form = st.number_input("Odds mín", 1.01, 10.0, float(st.session_state["odds_min"]), 0.05)
    odds_max_form = st.number_input("Odds máx", 1.01, 10.0, float(st.session_state["odds_max"]), 0.05)
    min_value_form = st.slider("💰 Value mínimo (%)", 0, 40, int(st.session_state["min_value"]))
    min_cover_prob_form = st.slider("🎯 Cover prob. mínima %", 0, 90, int(st.session_state["min_cover_prob"]))
    st.markdown("---")
    top_per_game_form = st.slider("🧹 Máx picks por jogo", 3, 20, int(st.session_state["top_per_game"]))
    static_plot_form = st.toggle("🌙 Gráfico estático", value=bool(st.session_state["static_plot"]))
    main_view_form = st.radio(
        "Visão principal",
        ["Cheat Sheet", "Por jogo", "Top picks"],
        index=["Cheat Sheet", "Por jogo", "Top picks"].index(st.session_state["main_view"]),
        horizontal=True,
    )
    submitted = st.form_submit_button("Aplicar filtros", width="stretch")

if submitted:
    st.session_state.update(
        {
            "leagues": leagues_form or ["soccer_epl"],
            "num_jogos": num_jogos_form,
            "w_recent": w_recent_form,
            "stat_sel": stat_sel_form,
            "side_sel": side_sel_form,
            "min_stars": min_stars_form,
            "odds_min": odds_min_form,
            "odds_max": odds_max_form,
            "min_value": min_value_form,
            "min_cover_prob": min_cover_prob_form,
            "top_per_game": top_per_game_form,
            "static_plot": static_plot_form,
            "player_search": player_search_form,
            "sort_by": sort_by_form,
            "main_view": main_view_form,
            "selected_rec_id": None,
        }
    )

cfg = {key: st.session_state[key] for key in DEFAULT_FILTERS}
selected_leagues = tuple(cfg["leagues"] or ["soccer_epl"])

fetch_start = perf_counter()
with st.spinner("🔍 Buscando linhas (gols, escanteios, cartões, SOT)..."):
    odds_result = get_football_props(API_KEY, selected_leagues)
    raw_props = odds_result.get("props", [])
fetch_seconds = perf_counter() - fetch_start

if not raw_props:
    st.error("❌ Não encontrei linhas da Odds API agora.")
    if odds_result.get("error"):
        st.caption(f"Motivo: {odds_result['error']}")
    st.caption(
        f"Eventos retornados: {odds_result.get('events_total', 0)} · "
        f"consultados: {odds_result.get('events_considered', 0)}"
    )
    if odds_result.get("event_errors"):
        with st.expander("Ver erros"):
            for item in odds_result["event_errors"]:
                st.write(f"- {item}")
    st.stop()

bookmaker_options = ["Todos"] + sorted({str(prop["bookmaker"]) for prop in raw_props})
game_options = ["Todos"] + sorted({str(prop["game"]) for prop in raw_props})

with st.sidebar:
    st.markdown("---")
    cfg["bookmaker_sel"] = st.selectbox(
        "🎲 Bookmaker",
        bookmaker_options,
        index=bookmaker_options.index(cfg["bookmaker_sel"]) if cfg["bookmaker_sel"] in bookmaker_options else 0,
    )
    cfg["game_sel"] = st.selectbox(
        "🏟️ Jogo",
        game_options,
        index=game_options.index(cfg["game_sel"]) if cfg["game_sel"] in game_options else 0,
    )
    st.session_state["bookmaker_sel"] = cfg["bookmaker_sel"]
    st.session_state["game_sel"] = cfg["game_sel"]

props = raw_props
if cfg["bookmaker_sel"] != "Todos":
    props = [prop for prop in props if prop["bookmaker"] == cfg["bookmaker_sel"]]
if cfg["stat_sel"] != "Todas":
    props = [prop for prop in props if prop["stat"] == cfg["stat_sel"]]
if cfg["player_search"].strip():
    needle = cfg["player_search"].strip().lower()
    props = [
        prop
        for prop in props
        if needle in str(prop["subject"]).lower() or needle in str(prop["game"]).lower()
    ]
props = [
    prop
    for prop in props
    if cfg["odds_min"] <= float(prop["odds_over"]) <= cfg["odds_max"]
    and cfg["odds_min"] <= float(prop["odds_under"]) <= cfg["odds_max"]
]
props = dedupe_props(props)
if not props:
    st.warning("Nenhuma linha passou nos filtros de mercado/odds.")
    st.stop()

st.success(f"✅ {len(props)} linhas após filtros de odds")

unmatched_teams: set[str] = set()
recs: list[dict[str, Any]] = []
analysis_start = perf_counter()
progress = st.progress(0)
status = st.empty()

for index, prop in enumerate(props, start=1):
    progress.progress(index / len(props))
    status.text(f"Modelando {prop['subject']} · {STAT_LABEL.get(prop['stat'], prop['stat'])} ({index}/{len(props)})")
    league = LEAGUES.get(str(prop["sport_key"]), {})
    div = league.get("div")
    history = load_league_history(div) if div else pd.DataFrame()
    stat = str(prop["stat"])
    line_value = float(prop["line"])

    if prop["kind"] == "player":
        home_fd = resolve_team_name(prop["home_team"], div) if div else None
        away_fd = resolve_team_name(prop["away_team"], div) if div else None
        resolved = resolve_player_sot(prop["subject"], league.get("fbref", "")) if league.get("fbref") else None
        if resolved is None:
            continue
        sot90, squad_name = resolved
        opponent, opponent_venue = None, "home"
        if squad_name and div:
            squad_fd = resolve_team_name(squad_name, div)
            if squad_fd and squad_fd == away_fd:
                opponent, opponent_venue = home_fd, "home"
            elif squad_fd and squad_fd == home_fd:
                opponent, opponent_venue = away_fd, "away"
        # Static SoT/90 ignores who the player is actually facing; scale it by how many shots
        # on target that specific opponent has been conceding lately vs the league average.
        mu = float(sot90) * opponent_sot_adjustment(history, opponent, opponent_venue)
        sd = max(0.7, math.sqrt(max(0.4, mu)))
        home_totals = pd.Series(dtype=float)
        away_totals = pd.Series(dtype=float)
    else:
        if history.empty or not div:
            continue
        home_fd = resolve_team_name(prop["home_team"], div)
        away_fd = resolve_team_name(prop["away_team"], div)
        if not home_fd:
            unmatched_teams.add(prop["home_team"])
        if not away_fd:
            unmatched_teams.add(prop["away_team"])
        if not home_fd or not away_fd:
            continue
        modeled = expected_match_total(history, home_fd, away_fd, stat, int(cfg["num_jogos"]), float(cfg["w_recent"]))
        if not modeled:
            continue
        mu, sd, home_totals, away_totals = modeled

    p_over = over_probability(stat, line_value, mu, sd)
    p_under = 1.0 - p_over
    val_over = p_over * 100.0 - implied_percent(float(prop["odds_over"]))
    val_under = p_under * 100.0 - implied_percent(float(prop["odds_under"]))
    side, model_value = ("Over", val_over) if val_over >= val_under else ("Under", val_under)
    if model_value < float(cfg["min_value"]):
        continue

    chosen_prob = p_over * 100.0 if side == "Over" else p_under * 100.0
    chosen_odds = float(prop["odds_over"] if side == "Over" else prop["odds_under"])
    chosen_book = prop["bookmaker_over"] if side == "Over" else prop["bookmaker_under"]
    fair_side = float(prop["prob_book_fair_over"] if side == "Over" else 100.0 - prop["prob_book_fair_over"])

    l5_home = side_hit_rate(home_totals.tail(5), line_value, side)
    l10_home = side_hit_rate(home_totals.tail(10), line_value, side)
    l5_away = side_hit_rate(away_totals.tail(5), line_value, side)
    l10_away = side_hit_rate(away_totals.tail(10), line_value, side)
    rates_l5 = [rate for rate in (l5_home, l5_away) if rate is not None]
    rates_l10 = [rate for rate in (l10_home, l10_away) if rate is not None]
    l5 = sum(rates_l5) / len(rates_l5) if rates_l5 else None
    l10 = sum(rates_l10) / len(rates_l10) if rates_l10 else None

    h2h_txt = "—"
    if prop["kind"] == "match" and home_fd and away_fd and not history.empty:
        h2h = h2h_totals(history, home_fd, away_fd, stat)
        if not h2h.empty:
            hits = int(side_hit_mask(h2h, line_value, side).sum())
            h2h_txt = f"{hits}/{len(h2h)} ({hits / len(h2h) * 100:.0f}%)"

    stars = compute_star_rating(model_value, chosen_prob)
    stake_pct = kelly_fraction(chosen_prob, chosen_odds)
    recs.append(
        {
            "rec_id": build_rec_id(prop["subject"], stat, line_value, prop["game"], side),
            "sport_key": prop["sport_key"],
            "kind": prop["kind"],
            "Liga": prop["league_name"],
            "Jogo": prop["game"],
            "Mercado": prop["subject"] if prop["kind"] == "player" else STAT_LABEL[stat],
            "Jogador/Time": prop["subject"],
            "Stat": stat,
            "Linha": line_value,
            "Lado": side,
            "Pick": f"{side} {line_value:g}",
            "Odds": chosen_odds,
            "Melhor Book": chosen_book,
            "Prob (modelo) %": round(chosen_prob, 1),
            "Prob justa book %": round(fair_side, 1),
            "Projeção": round(mu, 2),
            "Diff vs linha": round(mu - line_value, 2),
            "Estrelas": stars,
            "Rating": format_stars(stars),
            "Stake (½ Kelly) %": round(stake_pct, 2),
            "L5 %": round(l5, 1) if l5 is not None else None,
            "L10 %": round(l10, 1) if l10 is not None else None,
            "H2H": h2h_txt,
            "Value (modelo) %": round(model_value, 1),
            "away_team": prop["away_team"],
            "home_team": prop["home_team"],
            "home_fd": home_fd,
            "away_fd": away_fd,
        }
    )

progress.empty()
status.empty()
analysis_seconds = perf_counter() - analysis_start

if unmatched_teams:
    st.caption("Times sem match no CSV: " + ", ".join(sorted(unmatched_teams)[:8]))

if not recs:
    st.warning(f"Nenhuma aposta com value ≥ {cfg['min_value']}%. Afrouxe odds ou value mínimo.")
    st.stop()

recs = [
    rec
    for rec in recs
    if int(rec["Estrelas"]) >= int(cfg["min_stars"])
    and float(rec["Prob (modelo) %"]) >= float(cfg["min_cover_prob"])
    and (cfg["game_sel"] == "Todos" or rec["Jogo"] == cfg["game_sel"])
    and (cfg["side_sel"] == "Todos" or rec["Lado"] == cfg["side_sel"])
]
if not recs:
    st.warning("Nenhuma pick passou em estrelas / lado / cover prob.")
    st.stop()

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.metric("Linhas analisadas", len(props))
with c2:
    st.metric("Picks com value", len(recs))
with c3:
    st.metric("Jogos", len({rec["Jogo"] for rec in recs}))
with c4:
    st.metric("5 estrelas", sum(1 for rec in recs if rec["Estrelas"] >= 5))
with c5:
    st.metric("Odds média", f"{sum(rec['Odds'] for rec in recs) / len(recs):.2f}")
st.caption(f"⏱️ Odds: {fetch_seconds:.1f}s · Análise: {analysis_seconds:.1f}s")

if cfg["sort_by"] == "Probabilidade":
    recs = sorted(recs, key=lambda item: (-item["Prob (modelo) %"], -item["Value (modelo) %"]))
elif cfg["sort_by"] == "Odds":
    recs = sorted(recs, key=lambda item: (-item["Odds"], -item["Value (modelo) %"]))
else:
    recs = sorted(recs, key=lambda item: (-item["Estrelas"], -item["Value (modelo) %"], -item["Prob (modelo) %"]))

SHEET_COLS = [
    "Rating",
    "Liga",
    "Jogo",
    "Jogador/Time",
    "Stat",
    "Pick",
    "Projeção",
    "Diff vs linha",
    "Prob (modelo) %",
    "Value (modelo) %",
    "Stake (½ Kelly) %",
    "L5 %",
    "L10 %",
    "H2H",
    "Odds",
    "Melhor Book",
]

grouped: dict[str, list[dict[str, Any]]] = {}
for rec in recs:
    grouped.setdefault(rec["Jogo"], []).append(rec)
game_order = sorted(grouped, key=lambda game: max(item["Value (modelo) %"] for item in grouped[game]), reverse=True)

st.markdown("<hr/>", unsafe_allow_html=True)
main_view = cfg["main_view"]

if main_view == "Cheat Sheet":
    st.header("📋 Cheat Sheet")
    st.caption("GOLS/ESC/CART = totais da partida. SOT = chutes ao gol do jogador.")
    df_sheet = pd.DataFrame(recs)
    st.dataframe(
        df_sheet[[col for col in SHEET_COLS if col in df_sheet.columns]],
        height=min(720, 48 + min(len(df_sheet), 18) * 36),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Rating": st.column_config.TextColumn("⭐", width="small"),
            "Projeção": st.column_config.NumberColumn(format="%.2f"),
            "Diff vs linha": st.column_config.NumberColumn(format="%+.2f"),
            "Prob (modelo) %": st.column_config.ProgressColumn("Cover %", format="%.1f%%", min_value=0, max_value=100),
            "Value (modelo) %": st.column_config.NumberColumn("Edge %", format="+%.1f"),
            "Stake (½ Kelly) %": st.column_config.NumberColumn("Stake %", format="%.2f%%"),
            "Odds": st.column_config.NumberColumn(format="%.2f"),
        },
    )

elif main_view == "Top picks":
    st.header("⭐ Top picks")
    top_recs = [rec for rec in recs if int(rec["Estrelas"]) >= 4][:20]
    if not top_recs:
        st.info("Nenhuma pick 4+ estrelas. Reduza o value mínimo.")
    for rec in top_recs:
        with st.container(border=True):
            col_a, col_b, col_c = st.columns([4, 1, 1])
            with col_a:
                st.markdown(
                    f"### {rec['Rating']} · {rec['Jogador/Time']} — {STAT_LABEL.get(rec['Stat'], rec['Stat'])} {rec['Pick']}"
                )
                st.markdown(
                    f"<span class='small-muted'>{rec['Liga']} · {rec['Jogo']} · "
                    f"projeção {rec['Projeção']:.2f} · cover {rec['Prob (modelo) %']:.1f}% · "
                    f"L5 {fmt_rate(rec.get('L5 %'))}</span>",
                    unsafe_allow_html=True,
                )
            with col_b:
                st.markdown(
                    f"<div class='value-positive' style='font-size:26px;text-align:center;'>+{rec['Value (modelo) %']:.1f}%</div>",
                    unsafe_allow_html=True,
                )
            with col_c:
                if st.button("📊 Detalhes", key=f"top_{rec['rec_id']}", width="stretch"):
                    st.session_state["selected_rec_id"] = rec["rec_id"]
                    st.rerun()
else:
    st.header("🎯 Por jogo")
    for game_idx, game in enumerate(game_order):
        items = grouped[game]
        render_items = items[: int(cfg["top_per_game"])]
        best_value = max(item["Value (modelo) %"] for item in items)
        label = f"⚽ {game} — {len(items)} picks · melhor +{best_value:.1f}%"
        with st.expander(label, expanded=(game_idx == 0), width="stretch"):
            for idx, rec in enumerate(render_items, start=1):
                with st.container(border=True):
                    col_a, col_b, col_c = st.columns([4, 1, 1])
                    with col_a:
                        st.markdown(
                            f"### {idx}. {rec['Rating']} {STAT_LABEL.get(rec['Stat'], rec['Stat'])} · "
                            f"{rec['Jogador/Time']} {rec['Pick']}"
                        )
                        st.markdown(
                            f"<span class='small-muted'>{rec['Melhor Book']} @ {rec['Odds']:.2f} · "
                            f"projeção {rec['Projeção']:.2f} (Δ {rec['Diff vs linha']:+.2f}) · "
                            f"L5 {fmt_rate(rec.get('L5 %'))} · H2H {rec['H2H']}</span>",
                            unsafe_allow_html=True,
                        )
                    with col_b:
                        klass = "value-positive" if rec["Value (modelo) %"] >= 0 else "value-negative"
                        st.markdown(
                            f"<div class='{klass}' style='font-size:26px;text-align:center;'>+{rec['Value (modelo) %']:.1f}%</div>",
                            unsafe_allow_html=True,
                        )
                    with col_c:
                        if st.button("📊 Detalhes", key=f"detail_{rec['rec_id']}", width="stretch"):
                            st.session_state["selected_rec_id"] = rec["rec_id"]
                            st.rerun()

selected_rec_id = st.session_state.get("selected_rec_id")
selected_lookup = {rec["rec_id"]: rec for rec in recs}
if selected_rec_id and selected_rec_id in selected_lookup:
    render_selected_detail(selected_lookup[selected_rec_id], static_plot=bool(cfg["static_plot"]))
else:
    st.markdown("<hr/>", unsafe_allow_html=True)
    st.info("Abra **Detalhes** em Top picks ou Por jogo para ver o histórico do mandante/visitante/H2H.")

st.markdown("<hr/>", unsafe_allow_html=True)
st.subheader("📋 Tabela completa")
df_all = pd.DataFrame(recs)
drop_cols = ["rec_id", "sport_key", "kind", "away_team", "home_team", "home_fd", "away_fd"]
df_display = df_all.drop(columns=drop_cols, errors="ignore")
st.dataframe(df_display, height=380, use_container_width=True, hide_index=True)

export_records = [{k: v for k, v in rec.items() if k not in drop_cols} for rec in recs]
col_csv, col_json = st.columns(2)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
with col_csv:
    st.download_button("📥 CSV", data=dataframe_to_csv_bytes(df_display), file_name=f"football_props_{stamp}.csv", mime="text/csv", width="stretch")
with col_json:
    st.download_button("📥 JSON", data=records_to_json(export_records), file_name=f"football_props_{stamp}.json", mime="application/json", width="stretch")

st.markdown("<hr/>", unsafe_allow_html=True)
st.info(
    """
**Como o modelo funciona**
- **Gols / escanteios / cartões:** média ataca + defende (mandante em casa, visitante fora), misturando temporada atual e anterior.
- **Chutes ao gol:** SoT/90 do FBref para o jogador, ajustado pela força defensiva do adversário (SOT concedido recente vs. média da liga).
- **Distribuição:** Binomial Negativa ajustada à média e variância reais de cada time (cai para Poisson quando os dados não mostram sobre-dispersão) — mais realista que Poisson puro para cartões/escanteios, que tendem a ter caudas mais gordas.
- **Value** = probabilidade do modelo − probabilidade implícita da odd (sem vig no lado justo da book, calculada como consenso entre todas as casas, não a melhor odd de cada lado juntas).
- **Stake (½ Kelly)** = sugestão de tamanho de aposta como % da banca, usando Kelly fracionário (metade do Kelly cheio) para reduzir variância. É apenas uma referência matemática — não é recomendação financeira, e o modelo pode estar errado.

**Dica:** comece só com Premier League para gastar menos créditos da Odds API.
"""
)
