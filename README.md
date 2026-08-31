# Football Props Cheat Sheet

Cheat sheet de props de futebol: compara linhas das casas (The Odds API) com projeções a partir do histórico de cada time.

Mercados:

- **Gols** (`totals`)
- **Escanteios** (`alternate_totals_corners`)
- **Cartões** (`alternate_totals_cards`)
- **Chutes ao gol** (`player_shots_on_target`, jogador)

## Fontes de dados

| Fonte | Para quê | Liga |
|---|---|---|
| **The Odds API** | Linhas/odds das casas | Todas |
| **football-data.co.uk** | Histórico (gols, escanteios, cartões, chutes) | Ligas domésticas europeias |
| **API-Football** | Histórico (gols, escanteios, cartões, chutes) | Brasileirão, Libertadores, Europa League |
| **FBref** | SoT/90 por jogador | Todas |

## Como rodar

```bash
cd streamlit_futebol
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.example.toml .streamlit\secrets.toml
```

No `.streamlit/secrets.toml`, configure:

- `ODDS_API_KEY` — chave da The Odds API (mesma usada no app de NBA).
- `API_FOOTBALL_KEY` — chave da API-Football (gratuita em dashboard.api-football.com), **somente** se quiser usar Brasileirão, Libertadores e Europa League.

```bash
streamlit run streamlit_app.py
```

## Notas

- Escanteios e cartões costumam aparecer melhor em books **UK/EU**; chutes ao gol de jogador, em books **US**. O app consulta `uk,eu,us`.
- Cada jogo consultado consome créditos da Odds API (mercados × regiões). Comece com 1–2 ligas.
- No início da temporada o modelo mistura a temporada atual com a anterior automaticamente.
- **Brasileirão / Libertadores / Europa League:** o histórico vem da API-Football. Escanteios, cartões e chutes a gol são hidratados apenas para os jogos mais recentes (limite `AF_MAX_STAT_FIXTURES` = 60), para não estourar a cota do plano gratuito — gols sempre vêm completos. Competições continentais têm poucos jogos por time, então as projeções são menos confiáveis que nas ligas domésticas.
