import concurrent.futures
import datetime
import difflib
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

# V4 extends the proven V3 pipeline. V3 remains untouched for rollback.
BASE_FILE = Path(__file__).with_name("news_agent-v3.py")
spec = importlib.util.spec_from_file_location("news_agent_v3", BASE_FILE)
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Breadth / selection controls
GOOGLE_QUERY_VARIANTS = int(os.getenv("GOOGLE_QUERY_VARIANTS", "3"))
GDELT_QUERY_VARIANTS = int(os.getenv("GDELT_QUERY_VARIANTS", "2"))
CANDIDATE_ARTICLES_PER_TOPIC = int(os.getenv("CANDIDATE_ARTICLES_PER_TOPIC", "14"))
FINAL_ARTICLES_PER_TOPIC = int(os.getenv("FINAL_ARTICLES_PER_TOPIC", "8"))
MAX_PER_PUBLISHER = int(os.getenv("MAX_PER_PUBLISHER", "2"))
NEWS_FETCH_WORKERS = int(os.getenv("NEWS_FETCH_WORKERS", "6"))
USE_AI_NEWS_SELECTION = os.getenv("USE_AI_NEWS_SELECTION", "true").lower() in {"1", "true", "yes", "on"}
AI_SELECTED_PER_TOPIC = int(os.getenv("AI_SELECTED_PER_TOPIC", "6"))

# Direct RSS feeds: selected for independent source diversity.
RSS_FEEDS = {
    "bbc_business": {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "topics": {"global economy", "international economy", "technology", "artificial intelligence"},
    },
    "bbc_world": {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "topics": {"global economy", "international economy", "south african economy"},
    },
    "bbc_technology": {
        "name": "BBC Technology",
        "url": "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "topics": {"technology", "artificial intelligence"},
    },
    "bbc_football": {
        "name": "BBC Football",
        "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "topics": {"arsenal football club", "arsenal fc"},
    },
    "techcrunch": {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "topics": {"technology", "artificial intelligence"},
    },
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "with",
    "at", "from", "by", "is", "are", "be", "as", "south", "africa", "african",
    "latest", "news", "price", "prices", "sector", "industry",
}

QUERY_EXPANSIONS = {
    "south african economy": [
        "South African economy",
        "South Africa GDP inflation unemployment growth",
        "South Africa business confidence consumer spending manufacturing logistics",
    ],
    "global economy": [
        "Global economy",
        "world economy inflation interest rates growth",
        "global markets central banks trade economic outlook",
    ],
    "international economy": [
        "International economy",
        "world economy inflation interest rates growth",
        "global markets central banks trade economic outlook",
    ],
    "zar exchange rate": [
        "ZAR exchange rate",
        "South African rand ZAR dollar pound euro",
        "rand currency South Africa markets",
    ],
    "technology": [
        "Technology",
        "technology companies chips cloud cybersecurity",
        "technology industry AI data centres semiconductors",
    ],
    "artificial intelligence": [
        "Artificial Intelligence",
        "AI companies models chips data centres",
        "AI regulation enterprise artificial intelligence",
    ],
    "interest rate in south africa": [
        "Interest Rate in South Africa",
        "SARB repo rate inflation South Africa",
        "South Africa interest rates Reserve Bank monetary policy",
    ],
    "property sector": [
        "South Africa commercial property",
        "South Africa REIT retail property office industrial",
        "South Africa shopping centres rentals property development",
    ],
    "commodity prices": [
        "Commodity prices",
        "commodities metals oil mining prices",
        "gold platinum palladium coal iron ore copper prices",
    ],
    "brent crude oil": [
        "Brent crude oil",
        "Brent oil OPEC crude prices",
        "oil market Brent supply demand",
    ],
    "gold price": [
        "Gold price",
        "gold bullion central banks price",
        "gold market miners South Africa",
    ],
    "silver price": [
        "Silver price",
        "silver market industrial demand",
        "precious metals silver price",
    ],
    "south african companies": [
        "South African Companies",
        "South Africa companies earnings trading updates",
        "JSE companies results acquisitions dividends",
    ],
    "johannesburg stock exchange jse": [
        "Johannesburg Stock Exchange JSE",
        "JSE shares results trading updates",
        "South African stocks earnings acquisitions dividends",
    ],
    "south african property industry": [
        "South African Property Industry",
        "South Africa commercial property REIT",
        "South Africa retail property office industrial development",
    ],
    "shopping centre developments south africa": [
        "Shopping Centre Developments South Africa",
        "South Africa shopping centre development retail property",
        "South Africa malls retail rentals vacancies footfall",
    ],
    "shopping centre developments": [
        "Shopping Centre Developments South Africa",
        "South Africa shopping centre development retail property",
        "South Africa malls retail rentals vacancies footfall",
    ],
    "arsenal football club": [
        "Arsenal Football Club",
        "Arsenal FC transfer injury match",
        "Arsenal Premier League Champions League",
    ],
    "arsenal fc": [
        "Arsenal Football Club",
        "Arsenal FC transfer injury match",
        "Arsenal Premier League Champions League",
    ],
}

SOURCE_TARGETED_DOMAINS = {
    "south african economy": ["moneyweb.co.za", "engineeringnews.co.za"],
    "zar exchange rate": ["moneyweb.co.za"],
    "interest rate in south africa": ["moneyweb.co.za"],
    "property sector": ["moneyweb.co.za", "engineeringnews.co.za"],
    "commodity prices": ["miningweekly.com", "engineeringnews.co.za"],
    "brent crude oil": ["engineeringnews.co.za"],
    "gold price": ["miningweekly.com"],
    "silver price": ["miningweekly.com"],
    "south african companies": ["moneyweb.co.za", "engineeringnews.co.za"],
    "johannesburg stock exchange jse": ["moneyweb.co.za"],
    "south african property industry": ["moneyweb.co.za", "engineeringnews.co.za"],
    "shopping centre developments south africa": ["moneyweb.co.za", "engineeringnews.co.za"],
    "shopping centre developments": ["moneyweb.co.za", "engineeringnews.co.za"],
    "technology": ["techcentral.co.za"],
    "artificial intelligence": ["techcentral.co.za"],
}


def normalise_topic(topic):
    return re.sub(r"\s+", " ", topic.strip().lower())


def build_queries(topic):
    key = normalise_topic(topic)
    base_queries = QUERY_EXPANSIONS.get(key, [topic, f"{topic} latest developments", f"{topic} analysis"])
    seen, ordered = set(), []
    for q in base_queries:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            ordered.append(q)
    return ordered


def source_targeted_query(topic):
    key = normalise_topic(topic)
    domains = SOURCE_TARGETED_DOMAINS.get(key, [])
    if not domains:
        return None
    # Rotate domain choice deterministically by day to add variety across runs.
    day_index = datetime.date.today().toordinal() % len(domains)
    domain = domains[day_index]
    return f"site:{domain} {topic}"


def clean_title(title):
    title = re.sub(r"\s+", " ", (title or "").strip())
    # Google News often appends " - Publisher" to the title.
    parts = title.rsplit(" - ", 1)
    if len(parts) == 2 and 1 <= len(parts[1].split()) <= 6:
        title = parts[0]
    return title.strip()


def normalised_title(title):
    text = clean_title(title).lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_tokens(title):
    return {w for w in normalised_title(title).split() if len(w) > 2 and w not in STOPWORDS}


def near_duplicate(a, b):
    na, nb = normalised_title(a), normalised_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = title_tokens(a), title_tokens(b)
    if ta and tb:
        jaccard = len(ta & tb) / max(1, len(ta | tb))
        if jaccard >= 0.72:
            return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.84


def parse_article_datetime(value):
    if not value:
        return None
    value = value.strip()
    # RFC822 (Google/RSS)
    try:
        dt = parsedate_to_datetime(value)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
    except Exception:
        pass
    # GDELT and ISO formats.
    for fmt, width in (
        ("%Y%m%dT%H%M%SZ", 16),
        ("%Y%m%dT%H%M%S", 15),
        ("%Y-%m-%dT%H:%M:%SZ", 20),
        ("%Y-%m-%d", 10),
    ):
        try:
            dt = datetime.datetime.strptime(value[:width], fmt)
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue
    return None


def article_score(article, topic):
    score = 0.0
    dt = parse_article_datetime(article.get("pubDate", ""))
    if dt:
        age_hours = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600)
        if age_hours <= 12:
            score += 40
        elif age_hours <= 24:
            score += 35
        elif age_hours <= 48:
            score += 30
        elif age_hours <= 72:
            score += 22
        elif age_hours <= 168:
            score += 12
        else:
            score += max(0, 8 - age_hours / 168)

    topic_words = title_tokens(topic)
    headline_words = title_tokens(article.get("title", ""))
    score += 6 * len(topic_words & headline_words)

    matched_query = (article.get("matched_query") or "").lower()
    if normalise_topic(topic) in matched_query:
        score += 6

    provider = article.get("provider", "")
    if provider.startswith("RSS"):
        score += 4  # independent direct-source bonus
    elif provider == "Google News":
        score += 3
    elif provider == "GDELT":
        score += 2

    return score


def copy_with_metadata(article, query):
    result = dict(article)
    result["title"] = clean_title(result.get("title", "No Title"))
    result["matched_query"] = query
    return result


def fetch_google_query(query):
    try:
        return [copy_with_metadata(a, query) for a in v3.base.fetch_google_news(query)]
    except Exception as e:
        print(f"⚠️ Google query failed for '{query}': {e}", file=sys.stderr)
        return []


def fetch_gdelt_query(query):
    try:
        return [copy_with_metadata(a, query) for a in v3.base.fetch_gdelt_news(query)]
    except Exception as e:
        print(f"⚠️ GDELT query failed for '{query}': {e}", file=sys.stderr)
        return []


def fetch_rss_feed(feed_key, feed):
    articles = []
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={
                "User-Agent": "Daily-News-Podcast/4.0",
                "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.7",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            print(f"   {feed['name']} RSS: HTTP {response.getcode()} | {len(raw)} bytes")
        root = ET.fromstring(raw)

        # RSS
        items = list(root.iter("item"))
        for item in items[:30]:
            title = item.findtext("title") or "No Title"
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or item.findtext("date") or ""
            articles.append({
                "title": clean_title(title),
                "link": link.strip(),
                "pubDate": pub.strip(),
                "source": feed["name"],
                "provider": f"RSS: {feed['name']}",
                "feed_key": feed_key,
            })

        # Atom fallback
        if not articles:
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//a:entry", ns)[:30]:
                title = entry.findtext("a:title", default="No Title", namespaces=ns)
                link_el = entry.find("a:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                pub = (
                    entry.findtext("a:published", default="", namespaces=ns)
                    or entry.findtext("a:updated", default="", namespaces=ns)
                )
                articles.append({
                    "title": clean_title(title),
                    "link": link.strip(),
                    "pubDate": pub.strip(),
                    "source": feed["name"],
                    "provider": f"RSS: {feed['name']}",
                    "feed_key": feed_key,
                })
    except Exception as e:
        print(f"⚠️ {feed['name']} RSS failed: {e}", file=sys.stderr)
    return articles


def fetch_all_rss_once():
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(RSS_FEEDS))) as executor:
        future_map = {
            executor.submit(fetch_rss_feed, key, feed): key
            for key, feed in RSS_FEEDS.items()
        }
        for future in concurrent.futures.as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"⚠️ RSS worker failed for {key}: {e}", file=sys.stderr)
                results[key] = []
    return results


def rss_matches_topic(article, topic):
    key = normalise_topic(topic)
    feed_key = article.get("feed_key")
    feed = RSS_FEEDS.get(feed_key, {})
    if key not in feed.get("topics", set()):
        return False

    # For broad feed/topic matches, require at least one meaningful keyword when possible.
    headline_words = title_tokens(article.get("title", ""))
    if key in {"global economy", "international economy"}:
        economic_terms = {
            "economy", "economic", "inflation", "rates", "bank", "growth", "trade",
            "markets", "market", "business", "tariff", "recession", "currency", "jobs",
        }
        return bool(headline_words & economic_terms)
    if key in {"technology", "artificial intelligence"}:
        if key == "artificial intelligence":
            ai_terms = {"ai", "artificial", "intelligence", "openai", "model", "models", "chip", "chips", "robot"}
            return bool(headline_words & ai_terms)
        return True
    if key in {"arsenal football club", "arsenal fc"}:
        return "arsenal" in normalised_title(article.get("title", ""))
    if key == "south african economy":
        text = normalised_title(article.get("title", ""))
        return "south africa" in text or "south african" in text
    return False


def deduplicate_and_rank(pool, topic):
    ranked = sorted(pool, key=lambda a: article_score(a, topic), reverse=True)
    unique = []
    for article in ranked:
        if not article.get("title"):
            continue
        if any(near_duplicate(article["title"], u["title"]) for u in unique):
            continue
        unique.append(article)
        if len(unique) >= CANDIDATE_ARTICLES_PER_TOPIC:
            break

    # Publisher diversity cap.
    selected = []
    publisher_counts = Counter()
    deferred = []
    for article in unique:
        publisher = (article.get("source") or article.get("provider") or "Unknown").strip().lower()
        if publisher_counts[publisher] < MAX_PER_PUBLISHER:
            selected.append(article)
            publisher_counts[publisher] += 1
        else:
            deferred.append(article)
        if len(selected) >= CANDIDATE_ARTICLES_PER_TOPIC:
            break

    # If diversity cap leaves us short, backfill with best deferred stories.
    for article in deferred:
        if len(selected) >= CANDIDATE_ARTICLES_PER_TOPIC:
            break
        selected.append(article)

    return selected[:CANDIDATE_ARTICLES_PER_TOPIC]


def gather_topic(topic, rss_cache):
    queries = build_queries(topic)
    google_queries = queries[:GOOGLE_QUERY_VARIANTS]
    gdelt_queries = queries[:GDELT_QUERY_VARIANTS]

    targeted = source_targeted_query(topic)
    if targeted:
        google_queries.append(targeted)

    pool = []
    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=NEWS_FETCH_WORKERS) as executor:
        for query in google_queries:
            tasks.append(executor.submit(fetch_google_query, query))
        for query in gdelt_queries:
            tasks.append(executor.submit(fetch_gdelt_query, query))

        for future in concurrent.futures.as_completed(tasks):
            try:
                pool.extend(future.result())
            except Exception as e:
                print(f"⚠️ News worker failed for '{topic}': {e}", file=sys.stderr)

    for feed_articles in rss_cache.values():
        for article in feed_articles:
            if rss_matches_topic(article, topic):
                enriched = dict(article)
                enriched["matched_query"] = f"Direct RSS match for {topic}"
                pool.append(enriched)

    candidates = deduplicate_and_rank(pool, topic)
    print(
        f"✓ '{topic}': {len(pool)} raw articles → "
        f"{len(candidates)} deduplicated/diverse candidates"
    )
    return candidates


def ai_select_news(news_by_topic):
    if not USE_AI_NEWS_SELECTION or not OPENAI_API_KEY or not v3.base.HAS_OPENAI:
        return {
            topic: articles[:FINAL_ARTICLES_PER_TOPIC]
            for topic, articles in news_by_topic.items()
        }

    payload = {}
    for topic, articles in news_by_topic.items():
        payload[topic] = [
            {
                "id": idx,
                "title": a.get("title", ""),
                "date": a.get("pubDate", ""),
                "source": a.get("source", ""),
                "provider": a.get("provider", ""),
            }
            for idx, a in enumerate(articles)
        ]

    client = v3.base.OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
You are selecting stories for a concise but wide-ranging twice-daily news and financial podcast.

For each topic below, choose up to {AI_SELECTED_PER_TOPIC} article IDs that together provide:
- the most important and current developments;
- different angles rather than duplicate coverage;
- source diversity;
- strong relevance to the topic;
- preference for the last 24-48 hours when importance is otherwise similar;
- preference for South African sources on South African topics;
- preference for credible international sources on global topics.

Return ONLY valid JSON in this exact shape:
{{"selections": {{"TOPIC": [0,1,2]}}}}

CANDIDATES:
{json.dumps(payload, ensure_ascii=False)}
"""
    try:
        print("Running AI news-selection pass via OpenAI GPT-4o-mini...")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise news editor. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        parsed = json.loads(response.choices[0].message.content)
        selections = parsed.get("selections", {})
        final = {}
        for topic, articles in news_by_topic.items():
            ids = selections.get(topic, [])
            chosen = []
            seen = set()
            if isinstance(ids, list):
                for idx in ids:
                    if isinstance(idx, int) and 0 <= idx < len(articles) and idx not in seen:
                        chosen.append(articles[idx])
                        seen.add(idx)
                    if len(chosen) >= AI_SELECTED_PER_TOPIC:
                        break
            if not chosen:
                chosen = articles[:FINAL_ARTICLES_PER_TOPIC]
            final[topic] = chosen[:FINAL_ARTICLES_PER_TOPIC]
            print(f"   ✓ {topic}: {len(articles)} candidates → {len(final[topic])} selected")
        return final
    except Exception as e:
        print(f"⚠️ AI news selection failed; using rule-based ranking: {e}", file=sys.stderr)
        return {
            topic: articles[:FINAL_ARTICLES_PER_TOPIC]
            for topic, articles in news_by_topic.items()
        }


def print_source_summary(news):
    counts = Counter()
    for articles in news.values():
        for article in articles:
            source = article.get("source") or article.get("provider") or "Unknown"
            counts[source] += 1
    if counts:
        top = ", ".join(f"{name}: {count}" for name, count in counts.most_common(12))
        print(f"Selected source mix: {top}")


def main():
    print("==========================================")
    print("🚀 DAILY LONG-FORM PODCAST AGENT (V4): START")
    print("==========================================")
    print("News architecture: Google + GDELT in parallel + direct RSS + query expansion + deduplication")

    if v3.TTS_PROVIDER == "openai" and not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    print("Retrieving direct RSS feeds...")
    rss_cache = fetch_all_rss_once()

    candidate_news = {}
    for topic in v3.base.TOPICS:
        topic = topic.strip()
        if not topic:
            continue
        print(f"\nBuilding expanded news pool for: '{topic}'")
        articles = gather_topic(topic, rss_cache)
        if articles:
            candidate_news[topic] = articles
        else:
            print(f"⚠️ No usable articles found for '{topic}'")

    if not candidate_news:
        print("❌ No news articles could be fetched. Exiting.", file=sys.stderr)
        sys.exit(1)

    selected_news = ai_select_news(candidate_news)
    print_source_summary(selected_news)

    # Reuse V3's dedicated market data, script-length controls, exact market snapshot and TTS.
    market = v3.fetch_market_data()
    script = v3.generate_script(selected_news, market)

    Path("podcast_transcript.txt").write_text(script, encoding="utf-8")
    print("✓ Podcast transcript saved to 'podcast_transcript.txt'")
    v3.generate_audio(script, "podcast_briefing.mp3")

    print("==========================================")
    print("🎉 DAILY LONG-FORM PODCAST AGENT (V4): COMPLETE")
    print("==========================================")


if __name__ == "__main__":
    main()
