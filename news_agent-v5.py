import datetime
import importlib.util
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# V5 extends V4. V4 remains untouched for rollback.
BASE_FILE = Path(__file__).with_name("news_agent-v4.py")
spec = importlib.util.spec_from_file_location("news_agent_v4", BASE_FILE)
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# -----------------------------------------------------------------------------
# ACTIVE TOPICS
# Explicitly define the live topic set so removed topics cannot leak in through
# an older environment variable or inherited default.
# -----------------------------------------------------------------------------
ACTIVE_TOPICS = [
    "South African Economy",
    "Global Economy",
    "ZAR Exchange Rate",
    "Technology",
    "Artificial Intelligence",
    "Interest Rate in South Africa",
    "Property Sector",
    "Commodity prices",
    "Brent Crude Oil",
    "Gold Price",
    "Silver Price",
    "South African Companies",
    "Johannesburg Stock Exchange JSE",
    "JSE Major Share Price Moves",
    "South African Retail Sector",
    "South African Property Industry",
    "Shopping Centre Developments South Africa",
    "Cape Town News",
]

# Override V4's inherited topic list.
v4.v3.base.TOPICS = ACTIVE_TOPICS

# Remove football/Arsenal-specific feed and query configuration from the active
# runtime. V4 remains available unchanged as a rollback file.
v4.RSS_FEEDS.pop("bbc_football", None)
v4.QUERY_EXPANSIONS.pop("arsenal football club", None)
v4.QUERY_EXPANSIONS.pop("arsenal fc", None)

# Add focused query expansion for the new topics.
v4.QUERY_EXPANSIONS.update(
    {
        "south african retail sector": [
            "South African retail sector",
            "South Africa retail sales consumer spending retailers supermarkets fashion ecommerce",
            "South African retailers trading updates store openings closures margins consumer demand",
        ],
        "jse major share price moves": [
            "JSE biggest gainers losers share price moves reasons",
            "JSE shares surge plunge rally fall results trading update guidance acquisition",
            "South African stocks market movers share price reasons earnings commodities rand",
        ],
        "cape town news": [
            "Cape Town news",
            "Cape Town economy business development infrastructure transport property tourism",
            "Cape Town city government housing energy water public safety major developments",
        ],
    }
)

# Strengthen source-targeted discovery for the new sections.
v4.SOURCE_TARGETED_DOMAINS.update(
    {
        "south african retail sector": [
            "moneyweb.co.za",
            "bizcommunity.com",
            "businesstech.co.za",
        ],
        "jse major share price moves": [
            "moneyweb.co.za",
            "businessday.co.za",
        ],
        "cape town news": [
            "capetownetc.com",
            "news24.com",
            "capetown.gov.za",
        ],
    }
)


# -----------------------------------------------------------------------------
# MARKET DATA OVERRIDES
# Keep ZAR/USD, GBP, EUR and SAR reference rates, and add USD/SAR.
# Remove platinum and palladium from the dedicated commodity-price snapshot.
# -----------------------------------------------------------------------------
def fetch_json(url, label):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Daily-News-Podcast/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        print(f"   {label}: HTTP {response.getcode()} | {len(raw)} bytes")
    return json.loads(raw.decode("utf-8", errors="replace"))


def fetch_exchange_rates():
    """Latest available ZAR reference rates plus USD/SAR from Frankfurter."""
    rates = {}

    # Existing ZAR crosses.
    try:
        url = "https://api.frankfurter.dev/v2/rates?" + urllib.parse.urlencode(
            {"base": "ZAR", "quotes": "USD,GBP,EUR,SAR"}
        )
        payload = fetch_json(url, "Frankfurter ZAR FX")
        if not isinstance(payload, list):
            raise ValueError("Unexpected Frankfurter ZAR response format")

        for row in payload:
            quote = row.get("quote")
            raw_rate = row.get("rate")
            if quote in {"USD", "GBP", "EUR", "SAR"} and raw_rate:
                raw_rate = float(raw_rate)
                if raw_rate > 0:
                    rates[quote] = {
                        "zar_per_unit": 1.0 / raw_rate,
                        "date": row.get("date", ""),
                        "source": "Frankfurter",
                    }

        for code in ("USD", "GBP", "EUR", "SAR"):
            if code in rates:
                print(
                    f"   ✓ {code}/ZAR R{rates[code]['zar_per_unit']:.4f} "
                    f"({rates[code]['date']})"
                )
            else:
                print(f"   ⚠️ {code}/ZAR unavailable")
    except Exception as e:
        print(f"⚠️ ZAR exchange-rate feed failed: {e}", file=sys.stderr)

    # New USD -> Saudi riyal cross-rate.
    try:
        url = "https://api.frankfurter.dev/v2/rates?" + urllib.parse.urlencode(
            {"base": "USD", "quotes": "SAR"}
        )
        payload = fetch_json(url, "Frankfurter USD/SAR")
        if not isinstance(payload, list):
            raise ValueError("Unexpected Frankfurter USD/SAR response format")

        for row in payload:
            if row.get("quote") == "SAR" and row.get("rate"):
                rates["USD_SAR"] = {
                    "sar_per_usd": float(row["rate"]),
                    "date": row.get("date", ""),
                    "source": "Frankfurter",
                }
                print(
                    f"   ✓ USD/SAR {rates['USD_SAR']['sar_per_usd']:.4f} "
                    f"({rates['USD_SAR']['date']})"
                )
                break
        if "USD_SAR" not in rates:
            print("   ⚠️ USD/SAR unavailable")
    except Exception as e:
        print(f"⚠️ USD/SAR exchange-rate feed failed: {e}", file=sys.stderr)

    return rates


def fetch_metals():
    """Real-time gold and silver only; platinum and palladium are excluded."""
    names = {"XAU": "Gold", "XAG": "Silver"}
    result = {}
    for symbol, name in names.items():
        try:
            payload = fetch_json(
                f"https://api.gold-api.com/price/{symbol}",
                f"Gold API {symbol}",
            )
            result[symbol] = {
                "name": name,
                "price_usd": float(payload["price"]),
                "updated_at": payload.get("updatedAt", ""),
                "source": "Gold API",
            }
            print(f"   ✓ {name}: ${result[symbol]['price_usd']:,.2f}/oz")
        except Exception as e:
            print(f"⚠️ {name} price feed failed: {e}", file=sys.stderr)
    return result


def fetch_market_data():
    print("Retrieving dedicated market data...")
    return {
        "exchange_rates": fetch_exchange_rates(),
        "metals": fetch_metals(),
        "brent": v4.v3.fetch_brent(),
    }


def market_context(market):
    lines = []
    labels = {
        "USD": "US dollar",
        "GBP": "British pound",
        "EUR": "euro",
        "SAR": "Saudi riyal",
    }

    for code in ("USD", "GBP", "EUR", "SAR"):
        item = market["exchange_rates"].get(code)
        if item:
            lines.append(
                f"1 {labels[code]} = {item['zar_per_unit']:.4f} ZAR "
                f"(reference date {item['date']})"
            )

    usd_sar = market["exchange_rates"].get("USD_SAR")
    if usd_sar:
        lines.append(
            f"1 US dollar = {usd_sar['sar_per_usd']:.4f} Saudi riyal "
            f"(reference date {usd_sar['date']})"
        )

    for symbol in ("XAU", "XAG"):
        item = market["metals"].get(symbol)
        if item:
            lines.append(
                f"{item['name']} = USD {item['price_usd']:.2f} per troy ounce "
                f"(updated {item['updated_at'] or 'latest available'})"
            )

    if market["brent"]:
        b = market["brent"]
        lines.append(
            f"Brent Europe spot = USD {b['price_usd']:.2f} per barrel "
            f"(observation date {b['date']})"
        )

    return "\n".join(lines) if lines else "No dedicated market data was available."


def exact_market_snapshot(market):
    paragraphs = []
    labels = {
        "USD": "US dollar",
        "GBP": "British pound",
        "EUR": "euro",
        "SAR": "Saudi riyal",
    }

    fx_parts, fx_dates = [], []
    for code in ("USD", "GBP", "EUR", "SAR"):
        item = market["exchange_rates"].get(code)
        if item:
            fx_parts.append(
                f"one {labels[code]} is {item['zar_per_unit']:.4f} South African rand"
            )
            if item.get("date"):
                fx_dates.append(item["date"])

    usd_sar = market["exchange_rates"].get("USD_SAR")
    if usd_sar:
        fx_parts.append(
            f"one US dollar is {usd_sar['sar_per_usd']:.4f} Saudi riyals"
        )
        if usd_sar.get("date"):
            fx_dates.append(usd_sar["date"])

    if fx_parts:
        date_text = (
            v4.v3.spoken_date(max(fx_dates))
            if fx_dates
            else "the latest available date"
        )
        paragraphs.append(
            f"For currencies, using the latest available reference rates dated {date_text}, "
            + "; ".join(fx_parts)
            + "."
        )
    else:
        paragraphs.append(
            "The dedicated exchange-rate feed was unavailable for this briefing."
        )

    metal_parts = []
    for symbol in ("XAU", "XAG"):
        item = market["metals"].get(symbol)
        if item:
            metal_parts.append(
                f"{item['name'].lower()} is {item['price_usd']:,.2f} US dollars per troy ounce"
            )

    if metal_parts:
        paragraphs.append("For precious metals, " + "; ".join(metal_parts) + ".")
    else:
        paragraphs.append(
            "The dedicated gold-and-silver price feed was unavailable for this briefing."
        )

    if market["brent"]:
        b = market["brent"]
        paragraphs.append(
            f"The latest published Brent Europe spot price, dated {v4.v3.spoken_date(b['date'])}, "
            f"is {b['price_usd']:,.2f} US dollars per barrel."
        )
    else:
        paragraphs.append("The dedicated Brent price feed was unavailable for this briefing.")

    paragraphs.append("That's your market snapshot and your briefing for today.")
    return "\n\n".join(paragraphs)


# -----------------------------------------------------------------------------
# SCRIPT OVERRIDE
# Add explicit editorial instructions for SA retail, Cape Town and JSE movers.
# -----------------------------------------------------------------------------
def generate_script(news, market):
    base = v4.v3.base
    if not base.HAS_OPENAI or not OPENAI_API_KEY:
        print("❌ OpenAI package or OPENAI_API_KEY is missing.", file=sys.stderr)
        sys.exit(1)

    client = base.OpenAI(api_key=OPENAI_API_KEY)

    news_text = ""
    for topic, articles in news.items():
        news_text += f"\n--- Topic: {topic} ---\n"
        for i, art in enumerate(articles, 1):
            source = " | ".join(
                x
                for x in [art.get("source", ""), art.get("provider", "")]
                if x
            )
            news_text += (
                f"{i}. {art['title']} ({art['pubDate']})"
                + (f" | Source: {source}" if source else "")
                + "\n"
            )

    greeting = "morning" if datetime.datetime.now().hour < 12 else "evening"
    today = datetime.date.today().strftime("%A, %B %d, %Y")
    mkt = market_context(market)

    prompt = f"""
You are an expert professional podcast host and financial journalist. Write a seamless, conversational 15 to 20-minute daily briefing.

TODAY'S NEWS DATA:
{news_text}

DEDICATED MARKET DATA (exact figures supplied by external feeds):
{mkt}

Requirements:
1. Tone: professional, energetic, intellectual and engaging.
2. Open with: "{greeting} briefing for {today}. I'm your host, and today we have a comprehensive update covering critical developments across economics, markets, South African business, retail, property and Cape Town."
3. Explain the important stories, why they matter, business/economic implications, and useful background. Use smooth transitions.
4. Give the South African retail sector a meaningful section when relevant. Cover retail sales, consumer demand, major retailers, trading updates, store expansion or closures, ecommerce, margins and other consequential retail developments supported by the supplied headlines.
5. Include a dedicated JSE market-movers section. Identify companies whose share prices made notable moves and explain the reported reason or catalyst when the supplied news supports it, such as results, guidance, trading updates, acquisitions, commodity moves, regulatory developments or other company-specific news. Never invent a percentage move, share price or reason. If the supplied coverage shows a move but does not establish why, say that the available coverage does not clearly establish the catalyst.
6. Include consequential Cape Town developments, prioritising the local economy, business, property/development, infrastructure, transport, tourism, municipal decisions, energy/water, housing and major public-safety developments.
7. Do not include Arsenal content.
8. Aim for about {v4.v3.TARGET_SCRIPT_WORDS:,} words and never intentionally return fewer than {v4.v3.MIN_SCRIPT_WORDS:,}. If news is sparse, add relevant educational context rather than filler.
9. Do not invent current prices, dates, results, quotes, percentage moves or breaking-news facts.
10. The system will append the exact exchange rates and commodity prices after your script. Do NOT recite those figures and do NOT give the final goodbye. Finish the analytical portion naturally so the market snapshot can follow.
11. Output only spoken words: no headings, labels or markdown.
"""

    system = (
        "You are a charismatic, accurate financial-news podcast writer. Produce speech-ready prose only. "
        "Prioritise consequential South African business, retail, JSE and Cape Town developments. "
        "Expand sparse news with useful educational context, never fabricated current facts."
    )

    print("Generating long-form podcast script via OpenAI GPT-4o-mini...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        script = response.choices[0].message.content.strip()
        words = len(script.split())
        print(f"✓ Initial draft: {words} words (~{words/140:.1f} minutes).")

        expansion = 0
        while (
            words < v4.v3.MIN_SCRIPT_WORDS
            and expansion < v4.v3.MAX_EXPANSION_PASSES
        ):
            expansion += 1
            print(
                f"⚠️ Below {v4.v3.MIN_SCRIPT_WORDS} words. "
                f"Expansion pass {expansion}/{v4.v3.MAX_EXPANSION_PASSES}..."
            )
            expand_prompt = f"""
Rewrite and expand the COMPLETE podcast body below to approximately {v4.v3.TARGET_SCRIPT_WORDS} words and at least {v4.v3.MIN_SCRIPT_WORDS} words.
Preserve its news facts, add useful context and implications, avoid repetitive filler, and do not invent current facts.
Retain meaningful coverage of South African retail, consequential Cape Town news, and JSE share-price movers with their reported catalysts when supported by the source headlines.
Do not include Arsenal content.
Do not recite exchange rates or commodity prices and do not give the final goodbye; an exact market snapshot is appended separately.
Output spoken words only.

CURRENT SCRIPT:
{script}
"""
            revised = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.65,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": expand_prompt},
                ],
            )
            candidate = revised.choices[0].message.content.strip()
            candidate_words = len(candidate.split())
            if candidate_words <= words:
                print(
                    f"⚠️ Expansion did not increase length ({candidate_words} words); "
                    "keeping prior draft."
                )
                break
            script, words = candidate, candidate_words
            print(f"✓ Expanded draft: {words} words (~{words/140:.1f} minutes).")

        snapshot = exact_market_snapshot(market)
        full = (
            script.rstrip()
            + "\n\nNow, let's close with today's market snapshot.\n\n"
            + snapshot
        )
        final_words = len(full.split())
        print(
            f"✓ Exact market snapshot appended. Final transcript: "
            f"{final_words} words (~{final_words/140:.1f} minutes)."
        )
        return full
    except Exception as e:
        print(f"❌ OpenAI script generation failed: {e}", file=sys.stderr)
        sys.exit(1)


# Monkey-patch the functions V4's main() calls, preserving the rest of the
# multi-source gathering, deduplication, ranking, AI selection and TTS pipeline.
v4.v3.fetch_market_data = fetch_market_data
v4.v3.generate_script = generate_script


def main():
    print("==========================================")
    print("🚀 DAILY LONG-FORM PODCAST AGENT (V5): START")
    print("==========================================")
    print(
        "V5 additions: SA retail + Cape Town + JSE movers, gold/silver-only metals, USD/SAR FX"
    )
    v4.main()


if __name__ == "__main__":
    main()
