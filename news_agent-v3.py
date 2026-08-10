import csv
import datetime
import importlib.util
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Reuse the proven Google News + GDELT gatherer and MP3 utilities from V2.
BASE_FILE = Path(__file__).with_name("news_agent-v2.py")
spec = importlib.util.spec_from_file_location("news_agent_v2", BASE_FILE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai").lower()
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "cedar")
OPENAI_TTS_INSTRUCTIONS = os.getenv(
    "OPENAI_TTS_INSTRUCTIONS",
    "Speak like an energetic, polished financial-news narrator. Sound confident, alert and engaging without becoming theatrical. Use crisp pronunciation, natural emphasis on important figures, and a slightly brisk but comfortable broadcast pace.",
)
MIN_SCRIPT_WORDS = int(os.getenv("MIN_SCRIPT_WORDS", "2200"))
TARGET_SCRIPT_WORDS = int(os.getenv("TARGET_SCRIPT_WORDS", "2600"))
MAX_EXPANSION_PASSES = int(os.getenv("MAX_EXPANSION_PASSES", "2"))


def fetch_json(url, label):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Daily-News-Podcast/3.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        print(f"   {label}: HTTP {response.getcode()} | {len(raw)} bytes")
    return json.loads(raw.decode("utf-8", errors="replace"))


def fetch_exchange_rates():
    """Latest available ZAR reference rates from Frankfurter, with no API key."""
    url = "https://api.frankfurter.dev/v2/rates?" + urllib.parse.urlencode(
        {"base": "ZAR", "quotes": "USD,GBP,EUR,SAR"}
    )
    rates = {}
    try:
        payload = fetch_json(url, "Frankfurter FX")
        if not isinstance(payload, list):
            raise ValueError("Unexpected Frankfurter response format")
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
        print(f"⚠️ Exchange-rate feed failed: {e}", file=sys.stderr)
    return rates


def fetch_metals():
    """Real-time precious metal prices from Gold API, with no API key."""
    names = {"XAU": "Gold", "XAG": "Silver", "XPT": "Platinum", "XPD": "Palladium"}
    result = {}
    for symbol, name in names.items():
        try:
            payload = fetch_json(f"https://api.gold-api.com/price/{symbol}", f"Gold API {symbol}")
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


def fetch_brent():
    """Latest published daily Brent Europe spot observation from FRED/U.S. EIA."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Daily-News-Podcast/3.0", "Accept": "text/csv,*/*;q=0.8"},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            text = response.read().decode("utf-8", errors="replace")
            print(f"   FRED Brent: HTTP {response.getcode()} | {len(text)} chars")
        latest = None
        for row in csv.DictReader(io.StringIO(text)):
            raw = (row.get("DCOILBRENTEU") or "").strip()
            if not raw or raw == ".":
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            date_value = row.get("observation_date") or row.get("DATE") or next(iter(row.values()), "")
            latest = {"price_usd": value, "date": date_value, "source": "FRED / U.S. EIA"}
        if latest:
            print(f"   ✓ Brent: ${latest['price_usd']:,.2f}/barrel ({latest['date']})")
        else:
            print("   ⚠️ No valid Brent observation returned")
        return latest
    except Exception as e:
        print(f"⚠️ Brent price feed failed: {e}", file=sys.stderr)
        return None


def fetch_market_data():
    print("Retrieving dedicated market data...")
    return {
        "exchange_rates": fetch_exchange_rates(),
        "metals": fetch_metals(),
        "brent": fetch_brent(),
    }


def spoken_date(value):
    if not value:
        return "the latest available date"
    try:
        d = datetime.datetime.strptime(value[:10], "%Y-%m-%d").date()
        return d.strftime("%A, %B %d, %Y").replace(" 0", " ")
    except Exception:
        return value


def market_context(market):
    lines = []
    labels = {"USD": "US dollar", "GBP": "British pound", "EUR": "euro", "SAR": "Saudi riyal"}
    for code in ("USD", "GBP", "EUR", "SAR"):
        item = market["exchange_rates"].get(code)
        if item:
            lines.append(f"1 {labels[code]} = {item['zar_per_unit']:.4f} ZAR (reference date {item['date']})")
    for symbol in ("XAU", "XAG", "XPT", "XPD"):
        item = market["metals"].get(symbol)
        if item:
            lines.append(f"{item['name']} = USD {item['price_usd']:.2f} per troy ounce (updated {item['updated_at'] or 'latest available'})")
    if market["brent"]:
        b = market["brent"]
        lines.append(f"Brent Europe spot = USD {b['price_usd']:.2f} per barrel (observation date {b['date']})")
    return "\n".join(lines) if lines else "No dedicated market data was available."


def exact_market_snapshot(market):
    paragraphs = []
    labels = {"USD": "US dollar", "GBP": "British pound", "EUR": "euro", "SAR": "Saudi riyal"}
    fx_parts, fx_dates = [], []
    for code in ("USD", "GBP", "EUR", "SAR"):
        item = market["exchange_rates"].get(code)
        if item:
            fx_parts.append(f"one {labels[code]} is {item['zar_per_unit']:.4f} South African rand")
            if item.get("date"):
                fx_dates.append(item["date"])
    if fx_parts:
        date_text = spoken_date(max(fx_dates)) if fx_dates else "the latest available date"
        paragraphs.append(
            f"For currencies, using the latest available reference rates dated {date_text}, "
            + "; ".join(fx_parts)
            + "."
        )
    else:
        paragraphs.append("The dedicated exchange-rate feed was unavailable for this briefing.")

    metal_parts = []
    for symbol in ("XAU", "XAG", "XPT", "XPD"):
        item = market["metals"].get(symbol)
        if item:
            metal_parts.append(f"{item['name'].lower()} is {item['price_usd']:,.2f} US dollars per troy ounce")
    if metal_parts:
        paragraphs.append("For precious metals, " + "; ".join(metal_parts) + ".")
    else:
        paragraphs.append("The dedicated precious-metals feed was unavailable for this briefing.")

    if market["brent"]:
        b = market["brent"]
        paragraphs.append(
            f"The latest published Brent Europe spot price, dated {spoken_date(b['date'])}, "
            f"is {b['price_usd']:,.2f} US dollars per barrel."
        )
    else:
        paragraphs.append("The dedicated Brent price feed was unavailable for this briefing.")

    paragraphs.append("That's your market snapshot and your briefing for today.")
    return "\n\n".join(paragraphs)


def generate_script(news, market):
    if not base.HAS_OPENAI or not OPENAI_API_KEY:
        print("❌ OpenAI package or OPENAI_API_KEY is missing.", file=sys.stderr)
        sys.exit(1)
    client = base.OpenAI(api_key=OPENAI_API_KEY)

    news_text = ""
    for topic, articles in news.items():
        news_text += f"\n--- Topic: {topic} ---\n"
        for i, art in enumerate(articles, 1):
            source = " | ".join(x for x in [art.get("source", ""), art.get("provider", "")] if x)
            news_text += f"{i}. {art['title']} ({art['pubDate']})" + (f" | Source: {source}" if source else "") + "\n"

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
2. Open with: "{greeting} briefing for {today}. I'm your host, and today we have a comprehensive update covering critical developments across economics, markets, South African tax and property."
3. Explain the important stories, why they matter, business/economic implications, and useful background. Use smooth transitions.
4. Aim for about {TARGET_SCRIPT_WORDS:,} words and never intentionally return fewer than {MIN_SCRIPT_WORDS:,}. If news is sparse, add relevant educational context rather than filler.
5. Do not invent current prices, dates, results, quotes or breaking-news facts.
6. The system will append the exact exchange rates and commodity prices after your script. Do NOT recite those figures and do NOT give the final goodbye. Finish the analytical portion naturally so the market snapshot can follow.
7. Output only spoken words: no headings, labels or markdown.
"""
    system = (
        "You are a charismatic, accurate financial-news podcast writer. Produce speech-ready prose only. "
        "Expand sparse news with useful educational context, never fabricated current facts."
    )

    print("Generating long-form podcast script via OpenAI GPT-4o-mini...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        script = response.choices[0].message.content.strip()
        words = len(script.split())
        print(f"✓ Initial draft: {words} words (~{words/140:.1f} minutes).")

        expansion = 0
        while words < MIN_SCRIPT_WORDS and expansion < MAX_EXPANSION_PASSES:
            expansion += 1
            print(f"⚠️ Below {MIN_SCRIPT_WORDS} words. Expansion pass {expansion}/{MAX_EXPANSION_PASSES}...")
            expand_prompt = f"""
Rewrite and expand the COMPLETE podcast body below to approximately {TARGET_SCRIPT_WORDS} words and at least {MIN_SCRIPT_WORDS} words.
Preserve its news facts, add useful context and implications, avoid repetitive filler, and do not invent current facts.
Do not recite exchange rates or commodity prices and do not give the final goodbye; an exact market snapshot is appended separately.
Output spoken words only.

CURRENT SCRIPT:
{script}
"""
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.65,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": expand_prompt}],
            )
            candidate = r.choices[0].message.content.strip()
            candidate_words = len(candidate.split())
            if candidate_words <= words:
                print(f"⚠️ Expansion did not increase length ({candidate_words} words); keeping prior draft.")
                break
            script, words = candidate, candidate_words
            print(f"✓ Expanded draft: {words} words (~{words/140:.1f} minutes).")

        snapshot = exact_market_snapshot(market)
        full = script.rstrip() + "\n\nNow, let's close with today's market snapshot.\n\n" + snapshot
        final_words = len(full.split())
        print(f"✓ Exact market snapshot appended. Final transcript: {final_words} words (~{final_words/140:.1f} minutes).")
        return full
    except Exception as e:
        print(f"❌ OpenAI script generation failed: {e}", file=sys.stderr)
        sys.exit(1)


def generate_audio(text, output_filename):
    chunks = base.split_script_into_chunks(text)
    output_path = Path(output_filename)
    chunk_files = []
    print(
        f"Generating audio in {len(chunks)} chunk(s) using {TTS_PROVIDER.upper()} "
        f"({OPENAI_TTS_MODEL}, voice={OPENAI_TTS_VOICE})..."
    )
    try:
        for idx, chunk in enumerate(chunks):
            chunk_file = os.path.abspath(f"temp_chunk_{idx}.mp3")
            chunk_files.append(chunk_file)
            print(f"Processing chunk {idx+1}/{len(chunks)} ({len(chunk)} characters)...")
            if TTS_PROVIDER == "openai":
                client = base.OpenAI(api_key=OPENAI_API_KEY)
                response = client.audio.speech.create(
                    model=OPENAI_TTS_MODEL,
                    voice=OPENAI_TTS_VOICE,
                    input=chunk,
                    instructions=OPENAI_TTS_INSTRUCTIONS,
                )
                response.write_to_file(chunk_file)
            else:
                if not base.HAS_GTTS:
                    raise RuntimeError("gTTS is not installed")
                base.gTTS(text=chunk, lang="en").save(chunk_file)

        if len(chunk_files) == 1:
            if output_path.exists():
                output_path.unlink()
            Path(chunk_files[0]).rename(output_path)
        else:
            base.concatenate_mp3_files(chunk_files, str(output_path))
            for item in chunk_files:
                if Path(item).exists():
                    Path(item).unlink()
        print(f"✓ Audio generated successfully: {output_filename}")
    except Exception as e:
        print(f"❌ Audio generation failed: {e}", file=sys.stderr)
        for item in chunk_files:
            if Path(item).exists():
                Path(item).unlink()
        sys.exit(1)


def main():
    print("==========================================")
    print("🚀 DAILY LONG-FORM PODCAST AGENT (V3): START")
    print("==========================================")

    if TTS_PROVIDER == "openai" and not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    news = {}
    for topic in base.TOPICS:
        topic = topic.strip()
        if not topic:
            continue
        print(f"Retrieving news for topic: '{topic}'...")
        articles, provider = base.fetch_news_with_fallback(topic)
        if articles:
            print(f"✓ Found {len(articles)} articles for '{topic}' via {provider}")
            news[topic] = articles
        else:
            print(f"⚠️ No articles found for '{topic}' from Google News or GDELT")

    if not news:
        print("❌ No news articles could be fetched. Exiting.", file=sys.stderr)
        sys.exit(1)

    market = fetch_market_data()
    script = generate_script(news, market)

    Path("podcast_transcript.txt").write_text(script, encoding="utf-8")
    print("✓ Podcast transcript saved to 'podcast_transcript.txt'")
    generate_audio(script, "podcast_briefing.mp3")

    print("==========================================")
    print("🎉 DAILY LONG-FORM PODCAST AGENT (V3): COMPLETE")
    print("==========================================")


if __name__ == "__main__":
    main()
