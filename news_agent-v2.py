import os
import sys
import urllib.request
import urllib.parse
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

# Default to the user's specific topics of interest
DEFAULT_TOPICS = (
    "South African Economy,"
    "International Economy,"
    "ZAR exchange rate,"
    "Commodity prices,"
    "Oil price,"
    "Gold price,"
    "Silver price,"
    "Platinum price,"
    "Palladium price,"
    "Interest rate in South Africa,"
    "Interest rate in UK,"
    "Interest rate in USA,"
    "South African Economic Indicators,"
    "South African Companies,"
    "Sasol,"
    "MTN,"
    "Oceana Group,"
    "Metair,"
    "Hulamin,"
    "Thungela,"
    "Telkom,"
    "Prosus,"
    "Exxaro,"
    "Reunert,"
    "Raubex,"
    "WBHO,"
    "Vodacom,"
    "ArcelorMittal,"
    "TFG,"
    "Cashbuild,"
    "African Rainbow Minerals,"
    "Sea Harvest,"
    "We Buy Cars,"
    "Johannesburg Stock Exchange JSE,"
    "South African Property Industry,"
    "Shopping Centre Developments,"
    "Technology,"
    "Arsenal Football Club"
)
TOPICS = os.getenv("NEWS_TOPICS", DEFAULT_TOPICS).split(",")
MAX_ARTICLES_PER_TOPIC = int(os.getenv("MAX_ARTICLES", "4"))
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai").lower()  # "gtts" (free) or "openai" (paid)
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")  # alloy, echo, fable, onyx, nova, shimmer

# ==========================================
# 1. NEWS GATHERER (Google News RSS - Free)
# ==========================================
def fetch_google_news(topic):
    """
    Fetches the latest articles for a topic using Google News RSS.
    Requires no API keys or external dependencies.
    """
    clean_topic = topic.strip()
    encoded_topic = urllib.parse.quote(clean_topic)
    url = f"https://news.google.com/rss/search?q={encoded_topic}&hl=en-US&gl=US&ceid=US:en"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    print(f"Retrieving news for topic: '{clean_topic}'...")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        articles = []
        
        # Parse RSS items
        for item in root.findall(".//item")[:MAX_ARTICLES_PER_TOPIC]:
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
            source = item.find("source").text if item.find("source") is not None else "Unknown Source"
            
            # Clean up title (Google News titles usually end with " - Source Name")
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
                
            articles.append({
                "title": title,
                "link": link,
                "date": pub_date,
                "source": source
            })
            
        print(f"✓ Found {len(articles)} articles for '{clean_topic}'")
        return articles
    except Exception as e:
        print(f"❌ Error fetching news for topic '{clean_topic}': {e}", file=sys.stderr)
        return []

# ==========================================
# 2. SYNTHESIZER (OpenAI gpt-4o-mini)
# ==========================================
def generate_podcast_script(all_news_data):
    """
    Sends the gathered news to OpenAI to write a highly engaging, conversational
    podcast script of 10-15 minutes (approx. 2,500 to 3,500 words).
    """
    if not HAS_OPENAI:
        print("❌ Error: 'openai' Python package is not installed.", file=sys.stderr)
        sys.exit(1)
        
    if not OPENAI_API_KEY:
        print("❌ Error: OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
        
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Format the gathered news into a readable text block for the LLM
    news_text = ""
    for topic, articles in all_news_data.items():
        news_text += f"\n--- TOPIC: {topic} ---\n"
        if not articles:
            news_text += "No recent articles found.\n"
            continue
        for idx, art in enumerate(articles, 1):
            news_text += f"{idx}. {art['title']} (Source: {art['source']}, Date: {art['date']})\n"

    # Determine whether this is a Morning or Evening update
    current_hour = datetime.datetime.now().hour
    time_of_day = "morning" if current_hour < 12 else "evening"
    greeting = "Good morning and welcome to your" if time_of_day == "morning" else "Good evening and welcome to your end-of-day"

    # Construct the podcast prompt
    prompt = f"""

You are an expert, professional podcast host and financial journalist, known for delivering deep, engaging daily briefings. 
Your job is to synthesize the following raw news articles into a seamless, highly engaging, and conversational 10 to 15-minute podcast episode.

    Here is today's raw news data:
    {news_text}

    Write a podcast script matching these exact guidelines:
    1. Tone: Professional, energetic, intellectual, and highly engaging (similar to NPR's Planet Money or Bloomberg's daily briefing).
    2. Structure:
        * Warm Introduction: Give a charismatic greeting for today's date.
        * Main Segments: Dedicate a solid, deeply detailed section to each and every topic. Group relevant articles, explain why these developments matter, connect the dots, and discuss their economic or real-world implications.
        * Smooth Transitions: Use professional, conversational transition phrases between segments to keep the audio flowing seamlessly.
        * Outro: A thoughtful sign-off wishing the listener a productive day (if morning) or a relaxed evening (if evening).
    3. Script Format: Output ONLY the spoken words. Do NOT include sound effect cues (e.g. '[Intro Music]'), speaker labels (e.g. 'Host:'), or markdown formatting (no bold asterisks, no bullet points, no hashtag headings). The output will be fed directly to a Text-to-Speech engine.
    4. Length & Sparse News Policy: The script MUST be between 2,500 and 3,500 words. If there is very little direct news data for a topic, do NOT shorten the script. Instead, thoroughly elaborate on the historical background of the companies, explain how their business model works, define key JSE or economic terms for the listener, and discuss the wider industry trends. Use this educational context to guarantee you hit the requested word length (aim for roughly 250 to 320 words per segment).
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
        # ==========================================
        # FIX: Added  to correctly index choices list
        # ==========================================
        script = response.choices.message.content.strip()
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
            
        # If a single paragraph is somehow larger than max_chars, split it by sentences
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
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
        for file in file_list:
            escaped_path = str(Path(file).resolve()).replace("'", "'\\''")
            temp_file.write(f"file '{escaped_path}'\n")
        list_file_path = temp_file.name

    try:
        cmd = [
            'ffmpeg', '-y', 
            '-f', 'concat', 
            '-safe', '0', 
            '-i', list_file_path, 
            '-c', 'copy', 
            str(output_path)
        ]
        print(f"Stitching {len(file_list)} audio chunks into a seamless podcast...")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print("✓ Concatenation complete!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ ffmpeg concatenation failed: {e.stderr.decode('utf-8')}", file=sys.stderr)
        return False
    finally:
        try:
            os.unlink(list_file_path)
        except Exception:
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
    
    print(f"Script split into {len(chunks)} chunks for speech synthesis.")
    temp_files = []
    
    try:
        for idx, chunk in enumerate(chunks, 1):
            temp_chunk_name = f"temp_chunk_{idx}.mp3"
            temp_files.append(temp_chunk_name)
            
            if TTS_PROVIDER == "openai":
                print(f"  → Synthesizing chunk {idx}/{len(chunks)} via OpenAI TTS ({OPENAI_TTS_VOICE})...")
                if not HAS_OPENAI or not OPENAI_API_KEY:
                    raise ValueError("OpenAI package or API Key missing. Falling back to gTTS.")
                
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.audio.speech.create(
                    model="tts-1",
                    voice=OPENAI_TTS_VOICE,
                    input=chunk
                )
                response.write_to_file(temp_chunk_name)
            else:
                print(f"  → Synthesizing chunk {idx}/{len(chunks)} via Google TTS (gTTS)...")
                if not HAS_GTTS:
                    raise ValueError("gTTS package is not installed.")
                
                tts = gTTS(text=chunk, lang="en", tld="com")
                tts.save(temp_chunk_name)
                
        # Concatenate all generated chunk files
        success = concatenate_mp3_files(temp_files, output_path)
        if not success:
            raise RuntimeError("Audio concatenation failed.")
            
    except Exception as e:
        print(f"❌ Voice synthesis failed or interrupted: {e}", file=sys.stderr)
        print("Falling back to unified gTTS generation...", file=sys.stderr)
        # Attempt to run a standard gTTS directly as a last-resort single file
        try:
            tts = gTTS(text=text, lang="en", tld="com")
            tts.save(str(output_path))
            print(f"✓ Fallback gTTS saved to: {output_path.resolve()}")
        except Exception as fallback_err:
            print(f"❌ Fallback gTTS also failed: {fallback_err}", file=sys.stderr)
            sys.exit(1)
            
    finally:
        # Clean up temporary chunk files
        print("Cleaning up temporary chunk files...")
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                print(f"  ⚠ Failed to delete {temp_file}: {e}", file=sys.stderr)

# ==========================================
# MAIN EXECUTION PIPELINE
# ==========================================
def main():
    print("==========================================")
    print("🚀 DAILY LONG-FORM PODCAST AGENT (V2): START")
    print("==========================================")
    
    # Track gathered news
    all_news_data = {}
    for topic in TOPICS:
        articles = fetch_google_news(topic)
        all_news_data[topic] = articles
        
    # Check if we got any news at all
    total_articles = sum(len(arts) for arts in all_news_data.values())
    if total_articles == 0:
        print("⚠ Warning: No news articles could be retrieved today. Exiting.")
        sys.exit(0)
        
    # Synthesize the script
    script = generate_podcast_script(all_news_data)
    
    # Save script transcript to a text file
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    current_hour = datetime.datetime.now().hour
    period = "morning" if current_hour < 12 else "evening"
    
    transcript_filename = f"podcast_transcript_{today_str}_{period}.txt"
    with open(transcript_filename, "w", encoding="utf-8") as f:
        f.write(script)
    print(f"✓ Written transcript saved to: {transcript_filename}")
    
    # Generate MP3 Audio file
    audio_filename = f"daily_briefing_{today_str}_{period}.mp3"
    generate_audio(script, audio_filename)
    
    print("\n==========================================")
    print("🎉 EXECUTION COMPLETED SUCCESSFULLY!")
    print(f"🎙 Audio: {audio_filename}")
    print(f"📄 Text:  {transcript_filename}")
    print("==========================================")

if __name__ == "__main__":
    main()
