import os
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import datetime
from pathlib import Path

# Try to import optional packages
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

# ==========================================
# CONFIGURATION (Loaded from environment variables)
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# We define the topics as a robust, single comma-separated string to avoid implicit tuple concatenation errors.
DEFAULT_TOPICS = (
    "South African Economy,International Economy,ZAR exchange rate,Commodity prices,Oil price,"
    "Gold price,Silver price,Platinum price,Palladium price,Interest rate in South Africa,"
    "South african tax,"
    "Technology,Artificial Intelligence,"
    "Interest rate in UK,Interest rate in USA,South African Economic Indicators,South African Companies,"
    "Sasol,MTN,Oceana Group,Metair,Hulamin,Thungela,Telkom,Prosus,Exxaro,Reunert,Raubex,WBHO,Vodacom,"
    "ArcelorMittal,TFG,Cashbuild,African Rainbow Minerals,Sea Harvest,We Buy Cars,Johannesburg Stock Exchange JSE,"
    "South African Property Industry,Shopping Centre Developments"
)

# Safely split topics into a list
NEWS_TOPICS_ENV = os.getenv("NEWS_TOPICS", "")
if NEWS_TOPICS_ENV:
    TOPICS = [t.strip() for t in NEWS_TOPICS_ENV.split(",") if t.strip()]
else:
    TOPICS = [t.strip() for t in DEFAULT_TOPICS.split(",") if t.strip()]

MAX_ARTICLES_PER_TOPIC = int(os.getenv("MAX_ARTICLES", "6"))
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai").lower()  # "gtts" (free) or "openai" (paid)
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "echo")  # alloy, echo, fable, onyx, nova, shimmer

# ==========================================
# 1. NEWS GATHERER (Google News RSS - Free)
# ==========================================
def fetch_google_news(topic):
    """
    Fetch latest articles from Google News RSS.
    Includes South African localisation, retries and better diagnostics.
    """
    clean_topic = topic.strip()

    # Try South Africa first, then US/global as fallback
    regions = [
        ("en-ZA", "ZA", "ZA:en"),
        ("en-US", "US", "US:en"),
    ]

    for hl, gl, ceid in regions:

        params = {
            "q": clean_topic,
            "hl": hl,
            "gl": gl,
            "ceid": ceid,
        }

        url = (
            "https://news.google.com/rss/search?"
            + urllib.parse.urlencode(params)
        )

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36"
                    ),
                    "Accept": (
                        "application/rss+xml,"
                        "application/xml;q=0.9,"
                        "text/xml;q=0.8,"
                        "*/*;q=0.7"
                    ),
                    "Accept-Language": "en-ZA,en;q=0.9",
                },
            )

            with urllib.request.urlopen(req, timeout=20) as response:

                status = response.getcode()
                content_type = response.headers.get(
                    "Content-Type",
                    ""
                )

                xml_data = response.read()

            print(
                f"   Google News response: "
                f"HTTP {status} | "
                f"{len(xml_data)} bytes | "
                f"{content_type}"
            )

            root = ET.fromstring(xml_data)

            # More robust than root.findall('.//item')
            items = list(root.iter("item"))

            if not items:
                print(
                    f"   ⚠️ Google returned no RSS items "
                    f"using region {gl}."
                )

                # Useful debugging output
                preview = xml_data[:300].decode(
                    "utf-8",
                    errors="replace"
                )

                print(
                    f"   Response preview: "
                    f"{preview[:300]}"
                )

                continue

            articles = []

            for item in items[:MAX_ARTICLES_PER_TOPIC]:

                title_elem = item.find("title")
                link_elem = item.find("link")
                pub_date_elem = item.find("pubDate")
                source_elem = item.find("source")

                title = (
                    title_elem.text.strip()
                    if title_elem is not None
                    and title_elem.text
                    else "No Title"
                )

                link = (
                    link_elem.text.strip()
                    if link_elem is not None
                    and link_elem.text
                    else ""
                )

                pub_date = (
                    pub_date_elem.text.strip()
                    if pub_date_elem is not None
                    and pub_date_elem.text
                    else ""
                )

                source = (
                    source_elem.text.strip()
                    if source_elem is not None
                    and source_elem.text
                    else ""
                )

                articles.append(
                    {
                        "title": title,
                        "link": link,
                        "pubDate": pub_date,
                        "source": source,
                    }
                )

            if articles:
                return articles

        except urllib.error.HTTPError as e:

            print(
                f"⚠️ Google News HTTP error "
                f"for '{clean_topic}': "
                f"{e.code} {e.reason}",
                file=sys.stderr,
            )

        except urllib.error.URLError as e:

            print(
                f"⚠️ Google News connection error "
                f"for '{clean_topic}': "
                f"{e.reason}",
                file=sys.stderr,
            )

        except ET.ParseError as e:

            print(
                f"⚠️ Google News returned invalid XML "
                f"for '{clean_topic}': {e}",
                file=sys.stderr,
            )

        except Exception as e:

            print(
                f"⚠️ Unexpected error fetching "
                f"'{clean_topic}': {e}",
                file=sys.stderr,
            )

    return []

# ==========================================
# 2. SYNTHESIZER (OpenAI gpt-4o-mini)
# ==========================================
def generate_podcast_script(all_news_data):
    """
    Sends the gathered news to OpenAI to write a highly engaging, conversational
    podcast script of 10-15 minutes.
    """
    if not HAS_OPENAI:
        print("❌ Error: 'openai' Python package is not installed.", file=sys.stderr)
        sys.exit(1)
        
    if not OPENAI_API_KEY:
        print("❌ Error: OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Initialize OpenAI Client
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Format gathered news text for the prompt
    news_text = ""
    for topic, articles in all_news_data.items():
        news_text += f"\n--- Topic: {topic} ---\n"
        for i, art in enumerate(articles, 1):
            news_text += f"{i}. {art['title']} ({art['pubDate']})\n"

    greeting = "morning" if datetime.datetime.now().hour < 12 else "evening"
    today_str = datetime.date.today().strftime('%A, %B %d, %Y')

    prompt = f"""
    You are an expert, professional podcast host and financial journalist, known for delivering daily briefings. Your job is to synthesize the news into a seamless, conversational 10 to 15-minute podcast episode.

    Here is today's raw news data:
    {news_text}

    Write a podcast script matching these exact guidelines:
    1. Tone: Professional, energetic, intellectual, and highly engaging.
    2. Structure:
        * Energetic Introduction: Give a charismatic greeting: "{greeting} briefing for {today_str}. I'm your host, and today we have a comprehensive update covering critical developments across economics, markets, south african tax and property."
        * Main Segments: Dedicate a solid, deeply detailed section to each and every topic. Explain why these developments matter, connect the dots, and discuss their economic or real-world implications.
        * Smooth Transitions: Use professional, conversational transition phrases between segments to keep the audio flowing.
        * Outro: Todays commodaties prices are Gold is trading at ..., silver at..., platinum at ..., palladium at ... and brent crude oil at .... The Rand is currently at ... to the US Dollar, ... to the GB Pound, ... to the Euro and ... to the Saudi Riyaal.
    3. Script Format: Output ONLY the spoken words. Do NOT include sound effect cues, speaker labels, or markdown formatting.
    4. Length & Sparse News Policy: The script MUST be between 2,500 and 4,500 words. If there is very little direct news data for a topic, do NOT shorten the script. Instead, thoroughly elaborate on the historical background of the companies, explain how their business model works, define key JSE or economic terms, and discuss the wider industry trends. Use this educational context to guarantee you hit the requested word length.
    """

    print("Generating long-form podcast script via OpenAI GPT-4o-mini...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[
                {
                    "role": "system", 
                    "content": "You are a professional, charismatic podcast narrator and financial journalist. You write text ready for speech synthesis with zero structural or markdown tags. When daily news is sparse, you masterfully expand the script with deeply detailed educational context, corporate histories, and economic explanations to ensure you always hit the exact word length requested."
                },
                {"role": "user", "content": prompt}
            ]
        )
        
        # Safe choice index parsing (bypasses system footnote stripping)
        script = response.choices[int(0)].message.content.strip()
        word_count = len(script.split())
        print(f"✓ Podcast script successfully generated! Word count: {word_count} words (~{(word_count/140):.1f} minutes of speech).")
        return script
    except Exception as e:
        print(f"❌ Error communicating with OpenAI API: {e}", file=sys.stderr)
        sys.exit(1)

# ==========================================
# 3. TEXT SPLITTING (OpenAI TTS 4096-char Limit)
# ==========================================
def split_script_into_chunks(text, max_chars=3800):
    """
    Splits a long script into smaller chunks, each below max_chars limit.
    Preserves paragraph boundaries for natural phrasing.
    """
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # If a single paragraph is larger than max_chars, split it by sentences
        if len(para) > max_chars:
            sentences = para.replace(". ", ".\n").split("\n")
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if current_length + len(sentence) + 1 > max_chars:
                    if current_chunk:
                        chunks.append(" ".join(current_chunk))
                    current_chunk = [sentence]
                    current_length = len(sentence)
                else:
                    current_chunk.append(sentence)
                    current_length += len(sentence) + 1
        else:
            if current_length + len(para) + 2 > max_chars:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_length = len(para)
            else:
                current_chunk.append(para)
                current_length += len(para) + 2
                
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return chunks

# ==========================================
# 4. AUDIO CONCATENATOR (ffmpeg)
# ==========================================
def concatenate_mp3_files(file_list, output_path):
    """
    Stitches multiple MP3 chunk files into a single, seamless MP3 using ffmpeg.
    """
    import subprocess
    import tempfile
    
    print("Stitching MP3 chunks together...")
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        for file_path in file_list:
            # ==========================================
            # CRITICAL PATH FIX: Ensure we write absolute paths
            # ==========================================
            abs_path = os.path.abspath(file_path)
            escaped_path = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")
        list_filename = f.name
        
    try:
        cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', 
            '-i', list_filename, '-c', 'copy', str(output_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise Exception(f"FFmpeg failed: {result.stderr.decode()}")
    finally:
        try:
            os.unlink(list_filename)
        except OSError:
            pass

# ==========================================
# 5. VOICE ARTIST (TTS Generation in Chunks)
# ==========================================
def generate_audio(text, output_filename):
    """
    Converts written script into an MP3 file using either gTTS or OpenAI TTS.
    Automatically handles chunking and stitching to stay within API limits.
    """
    output_path = Path(output_filename)
    chunks = split_script_into_chunks(text)
    
    print(f"Generating audio in {len(chunks)} chunk(s) using {TTS_PROVIDER.upper()}...")
    chunk_files = []
    
    try:
        for idx, chunk in enumerate(chunks):
            # ==========================================
            # PATH FIX: Define chunk files with absolute paths
            # ==========================================
            chunk_file = os.path.abspath(f"temp_chunk_{idx}.mp3")
            chunk_files.append(chunk_file)
            print(f"Processing chunk {idx+1}/{len(chunks)} ({len(chunk)} characters)...")
            
            if TTS_PROVIDER == "openai":
                if not HAS_OPENAI:
                    print("❌ Error: 'openai' Python package is not installed.", file=sys.stderr)
                    sys.exit(1)
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.audio.speech.create(
                    model="tts-1",
                    voice=OPENAI_TTS_VOICE,
                    input=chunk
                )
                response.write_to_file(chunk_file)
            else:
                if not HAS_GTTS:
                    print("❌ Error: 'gtts' Python package is not installed.", file=sys.stderr)
                    sys.exit(1)
                tts = gTTS(text=chunk, lang='en')
                tts.save(chunk_file)
                
        # Concat files together
        if len(chunk_files) == 1:
            if output_path.exists():
                output_path.unlink()
            Path(chunk_files[int(0)]).rename(output_path)
            print(f"✓ Audio generated successfully: {output_filename}")
        else:
            concatenate_mp3_files(chunk_files, str(output_path))
            for cf in chunk_files:
                if Path(cf).exists():
                    Path(cf).unlink()
            print(f"✓ Combined audio generated successfully: {output_filename}")
            
    except Exception as e:
        print(f"❌ Error during audio generation: {e}", file=sys.stderr)
        for cf in chunk_files:
            if Path(cf).exists():
                Path(cf).unlink()
        sys.exit(1)

# ==========================================
# MAIN EXECUTION PIPELINE
# ==========================================
def main():
    print("==========================================")
    print("🚀 DAILY LONG-FORM PODCAST AGENT (V2): START")
    print("==========================================")
    
    # Check API Key if using OpenAI
    if TTS_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            print("❌ Error: OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
            sys.exit(1)
            
    # Step 1: Fetch Google News
    all_news_data = {}
    for topic in TOPICS:
        if not topic.strip():
            continue
        print(f"Retrieving news for topic: '{topic.strip()}'...")
        articles = fetch_google_news(topic)
        if articles:
            print(f"✓ Found {len(articles)} articles for '{topic.strip()}'")
            all_news_data[topic.strip()] = articles
        else:
            print(f"⚠️ No articles found for '{topic.strip()}'")
            
    if not all_news_data:
        print("❌ Error: No news articles could be fetched for any topic. Exiting.", file=sys.stderr)
        sys.exit(1)
        
    # Step 2: Generate Podcast Script
    script = generate_podcast_script(all_news_data)
    
    # Save transcript to file
    transcript_path = Path("podcast_transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(script)
    print("✓ Podcast transcript saved to 'podcast_transcript.txt'")
    
    # Step 3: Generate Audio
    output_audio_file = "podcast_briefing.mp3"
    generate_audio(script, output_audio_file)
    
    print("==========================================")
    print("🎉 DAILY LONG-FORM PODCAST AGENT (V2): COMPLETE")
    print("==========================================")

if __name__ == "__main__":
    main()
