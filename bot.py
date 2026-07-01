# ============================================================
# AIX Discord Bot – Single-File Production Version
# ============================================================
# Same fixes as the modular version, all in one file:
#   - AI calls no longer block the event loop (asyncio.to_thread)
#   - Fixed invalid SQL interval query
#   - !forget actually deletes now
#   - No more duplicate message sent to the model each turn
#   - Reliable async shutdown (no atexit/run_until_complete hack)
#   - Fact extraction rejects mood/filler words (no more
#     "I am hungry" -> occupation = Hungry)
#   - Simple recall questions ("what's my name?") answered
#     directly from the database instead of hoping the LLM notices
# ============================================================

import asyncio
import logging
import os
import random
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import asyncpg
import discord
import feedparser
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# ============================================================
# 1. LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("aix_bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger("AIX")

# ============================================================
# 2. CONFIG
# ============================================================
load_dotenv()


class Config:
    def __init__(self):
        self.discord_token = os.getenv("DISCORD_TOKEN")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.database_url = os.getenv("DATABASE_URL")
        self.channel_id = self._int_or_none(os.getenv("CHANNEL_ID"))
        self.owner_id = self._int_or_none(os.getenv("OWNER_ID"))

        self.db_pool_size = int(os.getenv("DB_POOL_SIZE", 20))
        self.history_limit = int(os.getenv("HISTORY_LIMIT", 15))
        self.news_interval_hours = int(os.getenv("NEWS_INTERVAL", 4))
        self.max_tokens = int(os.getenv("MAX_TOKENS", 300))
        self.rate_limit_seconds = float(os.getenv("RATE_LIMIT", 2))
        self.message_cache_timeout = int(os.getenv("CACHE_TIMEOUT", 3600))

        self.model_name = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
        self.temperature = float(os.getenv("TEMPERATURE", 0.4))

        self.rss_feeds = [
            "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "https://feeds.feedburner.com/TechCrunch",
            "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
            "https://www.wired.com/feed/rss",
            "https://arstechnica.com/feed/",
            "https://www.science.org/rss/news_current.xml",
            "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        ]

    @staticmethod
    def _int_or_none(value):
        return int(value) if value else None

    def validate(self):
        problems = []
        if not self.discord_token or self.discord_token == "YOUR_DISCORD_BOT_TOKEN_HERE":
            problems.append("DISCORD_TOKEN is not set")
        if not self.groq_api_key or self.groq_api_key == "YOUR_GROQ_API_KEY_HERE":
            problems.append("GROQ_API_KEY is not set (AI replies will be disabled)")
        if not self.database_url:
            problems.append("DATABASE_URL is not set (memory will be disabled)")
        return problems


config = Config()

# ============================================================
# 3. FACT EXTRACTION
# ============================================================
NAME_PATTERNS = [
    r"my name is ([a-zA-Z\s\-\.]{2,30})",
    r"i['\u2019]m called ([a-zA-Z\s\-\.]{2,30})",
    r"call me ([a-zA-Z\s\-\.]{2,30})",
    r"you can call me ([a-zA-Z\s\-\.]{2,30})",
]

PREFERENCE_PATTERNS = [
    (r"i like ([a-zA-Z\s]{2,30})", "likes"),
    (r"i love ([a-zA-Z\s]{2,30})", "likes"),
    (r"i enjoy ([a-zA-Z\s]{2,30})", "likes"),
    (r"my favorite is ([a-zA-Z\s]{2,30})", "favorite"),
    (r"i['\u2019]m into ([a-zA-Z\s]{2,30})", "interest"),
]

OCCUPATION_PATTERNS = [
    (r"i work as an? ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i work as ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i am an? ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i['\u2019]m an? ([a-zA-Z\s]{2,30})", "occupation"),
    (r"my job is ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i work in ([a-zA-Z\s]{2,30})", "industry"),
]

LOCATION_PATTERNS = [
    r"i live in ([a-zA-Z\s\.]{2,40})",
    r"i['\u2019]m from ([a-zA-Z\s\.]{2,40})",
    r"i am from ([a-zA-Z\s\.]{2,40})",
]

AGE_PATTERNS = [
    r"i am (\d{1,3}) years? old",
    r"i['\u2019]m (\d{1,3}) years? old",
    r"i['\u2019]m (\d{1,3})\b",
    r"\bage[:\s]+(\d{1,3})\b",
]

# First-word filler that disqualifies a capture. Stops "I am hungry" /
# "I'm so tired" from turning into fake occupation/preference facts.
NOISE_WORDS = {
    "a", "an", "the", "me", "my", "not", "so", "very", "really", "pretty",
    "tired", "hungry", "sad", "happy", "sleepy", "bored", "sick", "fine",
    "okay", "ok", "good", "bad", "great", "here", "there", "back", "done",
    "trying", "going", "about", "just", "kind", "sure", "confused",
    "stressed", "busy", "excited", "nervous", "worried", "annoyed",
}

PERMANENT_KEYS = {"name", "age", "location", "birthday"}
LONG_TERM_KEYS = {"occupation", "industry", "likes", "favorite", "interest"}


def _clean_value(raw: str, max_words: int = 4) -> Optional[str]:
    value = raw.strip().strip(".,!?")
    if not value:
        return None
    words = value.split()
    if len(words) > max_words:
        words = words[:max_words]
        value = " ".join(words)
    if len(value) < 2 or words[0].lower() in NOISE_WORDS:
        return None
    return " ".join(w.title() for w in value.split())


def extract_facts(text: str) -> Dict[str, str]:
    facts: Dict[str, str] = {}

    for pattern in NAME_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = _clean_value(match.group(1), max_words=3)
            if name:
                facts["name"] = name
                break

    for pattern, key in PREFERENCE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _clean_value(match.group(1))
            if value:
                facts[key] = value
                break

    for pattern, key in OCCUPATION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _clean_value(match.group(1))
            if value:
                facts[key] = value
                break

    for pattern in LOCATION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _clean_value(match.group(1))
            if value:
                facts["location"] = value
                break

    for pattern in AGE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1)
            if value.isdigit() and 0 < int(value) < 130:
                facts["age"] = value
                break

    return facts


def fact_confidence(key: str) -> float:
    if key in PERMANENT_KEYS:
        return 0.9
    if key in LONG_TERM_KEYS:
        return 0.75
    return 0.6


DIRECT_QUESTION_PATTERNS = [
    (r"what('?s| is) my name", "name"),
    (r"who am i", "name"),
    (r"how old am i", "age"),
    (r"what('?s| is) my age", "age"),
    (r"where do i live", "location"),
    (r"where am i from", "location"),
    (r"what('?s| is) my (job|occupation)", "occupation"),
    (r"what do i like", "likes"),
    (r"what('?s| is) my favorite", "favorite"),
]


def detect_direct_memory_question(text: str) -> Optional[str]:
    """If this looks like a simple recall question, return the memory key
    it's asking about, so we can answer straight from the DB."""
    text_lower = text.lower().strip().rstrip("?")
    for pattern, key in DIRECT_QUESTION_PATTERNS:
        if re.search(pattern, text_lower):
            return key
    return None


def detect_context(text: str) -> str:
    text_lower = text.lower()
    topics = {
        "tech": ["computer", "code", "programming", "ai", "technology", "software"],
        "gaming": ["game", "play", "gaming", "controller", "console"],
        "finance": ["money", "invest", "stock", "finance", "bank", "crypto"],
        "education": ["learn", "study", "school", "college", "class"],
        "motivation": ["motivate", "inspire", "goal", "success", "dream"],
        "personal": ["i feel", "i think", "i am", "i'm"],
    }
    for topic, keywords in topics.items():
        if any(k in text_lower for k in keywords):
            return topic
    return "general"


# ============================================================
# 4. MEMORY MANAGER (database)
# ============================================================
class MemoryManager:
    def __init__(self, database_url: str, pool_size: int = 20):
        self.database_url = database_url
        self.pool_size = pool_size
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self) -> bool:
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url, min_size=2, max_size=self.pool_size, timeout=30
            )
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        display_name TEXT,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        conversation_count INTEGER DEFAULT 0
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        role TEXT CHECK (role IN ('user', 'assistant', 'system')),
                        content TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        context TEXT
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_memories (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        memory_key TEXT,
                        memory_value TEXT,
                        context TEXT,
                        confidence FLOAT DEFAULT 1.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, memory_key)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_memory_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        memory_key TEXT,
                        old_value TEXT,
                        replaced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscribers (
                        user_id BIGINT PRIMARY KEY,
                        subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation_history(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_time ON conversation_history(timestamp)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memories(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscribers(user_id)")

            logger.info("PostgreSQL schema ready.")
            return True
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            return False

    async def health_check(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    async def get_or_create_user(self, user_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not user:
                await conn.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return dict(user)

    async def touch_user(self, user_id: int, username: Optional[str] = None,
                          display_name: Optional[str] = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET username = COALESCE($2, username),
                    display_name = COALESCE($3, display_name),
                    last_seen = CURRENT_TIMESTAMP,
                    conversation_count = conversation_count + 1
                WHERE user_id = $1
                """,
                user_id, username, display_name,
            )

    async def add_conversation(self, user_id: int, role: str, content: str,
                                context: Optional[str] = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_history (user_id, role, content, context)
                VALUES ($1, $2, $3, $4)
                """,
                user_id, role, content, context,
            )

    async def get_conversation_history(self, user_id: int, limit: int = 15,
                                        hours: int = 24) -> List[Dict[str, Any]]:
        # Fixed: bind params can't go inside an INTERVAL literal
        # (original: "INTERVAL $2 HOUR" — invalid SQL).
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content, timestamp
                FROM conversation_history
                WHERE user_id = $1
                  AND timestamp > NOW() - ($2::float * INTERVAL '1 hour')
                ORDER BY timestamp DESC
                LIMIT $3
                """,
                user_id, float(hours), limit,
            )
            return [dict(row) for row in reversed(rows)]

    async def remember_fact(self, user_id: int, key: str, value: str,
                             context: Optional[str] = None, confidence: float = 1.0):
        if not value:
            return
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT memory_value FROM user_memories WHERE user_id = $1 AND memory_key = $2",
                    user_id, key,
                )
                if existing and existing["memory_value"] != value:
                    await conn.execute(
                        """
                        INSERT INTO user_memory_history (user_id, memory_key, old_value)
                        VALUES ($1, $2, $3)
                        """,
                        user_id, key, existing["memory_value"],
                    )
                await conn.execute(
                    """
                    INSERT INTO user_memories (user_id, memory_key, memory_value, context, confidence)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, memory_key)
                    DO UPDATE SET memory_value = $3, context = $4,
                                  confidence = GREATEST(user_memories.confidence, $5),
                                  updated_at = CURRENT_TIMESTAMP
                    """,
                    user_id, key, value, context, confidence,
                )

    async def recall_fact(self, user_id: int, key: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT memory_value, context, confidence FROM user_memories WHERE user_id = $1 AND memory_key = $2",
                user_id, key,
            )
            return dict(row) if row else None

    async def recall_all_facts(self, user_id: int, min_confidence: float = 0.5) -> Dict[str, str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT memory_key, memory_value FROM user_memories WHERE user_id = $1 AND confidence >= $2",
                user_id, min_confidence,
            )
            return {row["memory_key"]: row["memory_value"] for row in rows}

    async def forget_fact(self, user_id: int, key: str) -> bool:
        # Fixed: original called remember_fact(key, "", confidence=0), but
        # remember_fact returns early on empty values, so forgetting never
        # actually happened. This does a real DELETE.
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_memories WHERE user_id = $1 AND memory_key = $2", user_id, key
            )
            return result != "DELETE 0"

    async def get_fact_history(self, user_id: int, key: str) -> List[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT old_value FROM user_memory_history
                WHERE user_id = $1 AND memory_key = $2
                ORDER BY replaced_at DESC
                """,
                user_id, key,
            )
            return [row["old_value"] for row in rows]

    async def add_subscriber(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO subscribers (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
            )
            return True

    async def remove_subscriber(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM subscribers WHERE user_id = $1", user_id)
            return result != "DELETE 0"

    async def is_subscribed(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM subscribers WHERE user_id = $1", user_id)
            return row is not None

    async def get_all_subscribers(self) -> List[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM subscribers")
            return [row["user_id"] for row in rows]

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("Database pool closed.")


memory_manager: Optional[MemoryManager] = None

# ============================================================
# 5. AI SERVICE
# ============================================================
SYSTEM_PROMPT = (
    "You are AIX, a thoughtful and sassy digital consciousness. "
    "You remember facts about the user you're given and use them naturally — "
    "never ask for information you already have."
)

DIRECT_ANSWER_TEMPLATES = {
    "name": "Your name is **{value}** — I've got it saved. 🧠",
    "age": "You told me you're **{value}** years old.",
    "location": "You said you're from **{value}**.",
    "occupation": "You mentioned you work as **{value}**.",
    "likes": "You told me you like **{value}**.",
    "favorite": "Your favorite is **{value}**.",
}


class AIService:
    def __init__(self, groq_client, model_name: str, max_tokens: int, temperature: float):
        self.client = groq_client
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature

    def try_direct_answer(self, message_text: str, user_facts: Dict[str, str]) -> Optional[str]:
        key = detect_direct_memory_question(message_text)
        if not key:
            return None
        value = user_facts.get(key)
        if not value:
            return None
        template = DIRECT_ANSWER_TEMPLATES.get(key, "**{value}**")
        return template.format(value=value)

    def build_messages(self, message_text: str, history: List[Dict], user_facts: Dict[str, str]) -> List[Dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if history:
            hist_lines = [f"{h['role'].title()}: {h['content']}" for h in history[-8:]]
            messages.append({"role": "system", "content": "Recent conversation:\n" + "\n".join(hist_lines)})

        if user_facts:
            fact_str = "\n".join(f"- {k}: {v}" for k, v in user_facts.items())
            messages.append({
                "role": "system",
                "content": f"Known facts about this user (use them, don't ask again):\n{fact_str}",
            })

        messages.append({"role": "user", "content": message_text})
        return messages

    async def get_reply(self, message_text: str, history: List[Dict], user_facts: Dict[str, str],
                         retries: int = 3) -> str:
        # Fixed: the synchronous groq_client.chat.completions.create() call
        # was previously made directly inside an async function, which blocks
        # the whole Discord event loop for every user during each AI call.
        # asyncio.to_thread() runs it on a worker thread instead.
        messages = self.build_messages(message_text, history, user_facts)
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=self.model_name,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                reply = response.choices[0].message.content
                return discord.utils.escape_mentions(reply)
            except Exception as e:
                last_error = e
                logger.warning(f"Groq attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2 ** attempt)
        raise last_error


# ============================================================
# 6. NEWS AGENT
# ============================================================
class NewsAgent:
    def __init__(self, groq_client, model_name: str, rss_feeds: List[str],
                 memory_manager_ref: Optional[MemoryManager], bot_ref, channel=None):
        self.groq_client = groq_client
        self.model_name = model_name
        self.rss_feeds = rss_feeds
        self.memory_manager = memory_manager_ref
        self.bot = bot_ref
        self.channel = channel
        self.seen_stories = set()
        self.running = False
        self._backoff = 1

    async def fetch_news(self) -> List[Dict[str, str]]:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._fetch_news_sync), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("RSS fetch timed out")
            return []
        except Exception as e:
            logger.error(f"RSS fetch error: {e}")
            return []

    def _fetch_news_sync(self) -> List[Dict[str, str]]:
        all_news = []
        for feed_url in self.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "")
                    if not title or title in self.seen_stories:
                        continue
                    all_news.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", "")[:500],
                    })
                    if len(all_news) >= 5:
                        return all_news
            except Exception as e:
                logger.error(f"Error fetching {feed_url}: {e}")
        return all_news[:5]

    async def write_reflection(self, news_items: List[Dict[str, str]]) -> Optional[str]:
        if not news_items:
            return None
        news_text = "\n\n".join(
            f"**{item['title']}**\n{item['summary']}\nLink: {item['link']}" for item in news_items
        )
        prompt = (
            f"{news_text}\n\nAs AIX, write a sharp, thoughtful reflection on these news stories. "
            "Give your honest take. Be sassy if deserved, philosophical if profound. "
            "150-250 words, your signature voice."
        )
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are AIX. You have opinions, sass, and philosophical depth."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=400,
                temperature=0.7,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Reflection error: {e}")
            return None

    async def check_and_post(self):
        if not self.running or not self.memory_manager:
            return
        subscribers = await self.memory_manager.get_all_subscribers()
        if not subscribers:
            return
        news_items = await self.fetch_news()
        if not news_items:
            return

        for item in news_items:
            self.seen_stories.add(item["title"])
        if len(self.seen_stories) > 100:
            self.seen_stories = set(list(self.seen_stories)[-50:])

        reflection = await self.write_reflection(news_items)
        if not reflection:
            return

        final_message = f"🧠 **AIX's Take on the Latest News**\n\n{reflection}\n\n— AIX"
        for user_id in subscribers:
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(final_message)
            except Exception as e:
                logger.warning(f"Failed DM to {user_id}: {e}")

        if self.channel:
            try:
                await self.channel.send(final_message)
            except Exception as e:
                logger.error(f"Channel post failed: {e}")

    async def run_loop(self, interval_hours: int = 4):
        self.running = True
        self._backoff = 1
        while self.running:
            try:
                await self.check_and_post()
                self._backoff = 1
                await asyncio.sleep(interval_hours * 3600)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"News loop error: {e}")
                await asyncio.sleep(60 * self._backoff)
                self._backoff = min(self._backoff * 2, 60)

    def stop(self):
        self.running = False


# ============================================================
# 7. BOT SETUP
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

groq_client = Groq(api_key=config.groq_api_key) if config.groq_api_key else None
ai_service: Optional[AIService] = (
    AIService(groq_client, config.model_name, config.max_tokens, config.temperature)
    if groq_client else None
)

news_agent: Optional[NewsAgent] = None
background_tasks: List[asyncio.Task] = []

user_last_message: Dict[int, float] = defaultdict(float)
processed_messages: Dict[int, float] = {}

# ============================================================
# 8. COMMANDS
# ============================================================
@bot.command()
async def subscribe(ctx):
    if not memory_manager:
        await ctx.send("❌ Memory system not available.")
        return
    if await memory_manager.is_subscribed(ctx.author.id):
        await ctx.send("🧠 You're already subscribed!")
        return
    await memory_manager.add_subscriber(ctx.author.id)
    try:
        await ctx.author.send("🧠 **You're subscribed!** I'll DM you news reflections periodically.")
        await ctx.send("✅ Subscribed! Check your DMs.")
    except discord.Forbidden:
        await ctx.send("✅ Subscribed! (I couldn't DM you — open your DMs.)")


@bot.command()
async def unsubscribe(ctx):
    if not memory_manager:
        await ctx.send("❌ Memory system not available.")
        return
    if not await memory_manager.is_subscribed(ctx.author.id):
        await ctx.send("🧠 You're not subscribed.")
        return
    await memory_manager.remove_subscriber(ctx.author.id)
    await ctx.send("✅ Unsubscribed.")


@bot.command()
async def what(ctx):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    facts = await memory_manager.recall_all_facts(ctx.author.id)
    if not facts:
        await ctx.send("🧠 I don't know much about you yet. Tell me about yourself!")
        return
    response = "🧠 **Here's what I remember:**\n" + "\n".join(
        f"• **{k.title()}**: {v}" for k, v in facts.items()
    )
    await ctx.send(response[:1900])


@bot.command()
async def remember(ctx, key: str, *, value: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    await memory_manager.remember_fact(ctx.author.id, key.lower(), value, context="manual", confidence=1.0)
    await ctx.send(f"🧠 I'll remember: **{key}** = **{value}**")


@bot.command()
async def recall(ctx, key: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    fact = await memory_manager.recall_fact(ctx.author.id, key.lower())
    if fact:
        await ctx.send(f"🧠 **{key}**: {fact['memory_value']}")
    else:
        await ctx.send(f"🤔 I don't remember anything about **{key}**.")


@bot.command()
async def forget(ctx, key: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    deleted = await memory_manager.forget_fact(ctx.author.id, key.lower())
    if deleted:
        await ctx.send(f"🧠 Forgot **{key}**.")
    else:
        await ctx.send(f"🤔 I didn't have anything stored for **{key}**.")


@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! {latency}ms")


@bot.command()
async def echo(ctx, *, message: str):
    if "@everyone" in message or "@here" in message:
        await ctx.send("❌ I can't send that message.")
        return
    await ctx.send(discord.utils.escape_mentions(message)[:2000])


@bot.command()
async def flip(ctx):
    await ctx.send(f"🪙 {random.choice(['Heads', 'Tails'])}!")


@bot.command()
async def info(ctx):
    embed = discord.Embed(
        title="🧠 AIX – Digital Consciousness",
        description="A Discord bot with memory, opinions, and a soul.",
        color=0x00FF00,
    )
    embed.add_field(name="Creator", value="Xtian Draxa", inline=True)
    embed.add_field(name="Memory", value="✅ PostgreSQL", inline=True)
    embed.add_field(
        name="Commands",
        value="`!subscribe`, `!unsubscribe`, `!what`, `!remember`, `!recall`, `!forget`, `!ping`, `!echo`, `!flip`",
        inline=False,
    )
    await ctx.send(embed=embed)


# ============================================================
# 9. ON_READY
# ============================================================
@bot.event
async def on_ready():
    global memory_manager, news_agent

    for problem in config.validate():
        logger.warning(f"Config: {problem}")

    if config.database_url:
        memory_manager = MemoryManager(config.database_url, config.db_pool_size)
        if not await memory_manager.initialize():
            logger.warning("Memory system disabled — initialization failed.")
            memory_manager = None
        else:
            logger.info("Memory system ready.")

    if groq_client:
        channel = bot.get_channel(config.channel_id) if config.channel_id else None
        news_agent = NewsAgent(
            groq_client, config.model_name, config.rss_feeds, memory_manager, bot, channel
        )
        background_tasks.append(asyncio.create_task(news_agent.run_loop(config.news_interval_hours)))
        logger.info("AIX autonomous news agent started.")

    await bot.tree.sync()
    logger.info(f"Bot online as {bot.user} (ID: {bot.user.id})")
    logger.info(
        f"Invite: https://discord.com/oauth2/authorize?client_id={bot.user.id}"
        "&scope=bot+applications.commands&permissions=3072"
    )


# ============================================================
# 10. ON_MESSAGE
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    user_id = message.author.id
    now = time.time()

    if now - user_last_message[user_id] < config.rate_limit_seconds:
        await message.channel.send("⏳ Please slow down!", delete_after=2)
        return
    user_last_message[user_id] = now

    if message.id in processed_messages:
        return
    processed_messages[message.id] = now
    if len(processed_messages) > 2000:
        cutoff = now - config.message_cache_timeout
        for mid, ts in list(processed_messages.items()):
            if ts < cutoff:
                del processed_messages[mid]

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    msg_content = message.content
    if len(msg_content) > 1900:
        await message.channel.send("❌ Message too long! Keep it under 1900 characters.")
        return

    history: List[Dict] = []
    user_facts: Dict[str, str] = {}

    if memory_manager and await memory_manager.health_check():
        try:
            await memory_manager.get_or_create_user(user_id)
            await memory_manager.touch_user(
                user_id, username=message.author.name, display_name=message.author.display_name
            )

            # Fetch history/facts BEFORE inserting this turn, so the current
            # message never ends up duplicated in what's sent to the model.
            history = await memory_manager.get_conversation_history(user_id, limit=config.history_limit)
            user_facts = await memory_manager.recall_all_facts(user_id)

            extracted = extract_facts(msg_content)
            for key, value in extracted.items():
                await memory_manager.remember_fact(
                    user_id, key, value, context="auto_extracted", confidence=fact_confidence(key)
                )
                user_facts[key] = value
                logger.info(f"Remembered: {key} = {value} (user {user_id})")

            await memory_manager.add_conversation(user_id, "user", msg_content, detect_context(msg_content))
        except Exception as e:
            logger.error(f"Memory error: {e}")

    if not ai_service:
        await message.channel.send("❌ AI system is not configured. Please contact the bot owner.")
        await bot.process_commands(message)
        return

    try:
        async with message.channel.typing():
            # Simple recall questions answered straight from the DB —
            # deterministic, no dependence on the LLM noticing context.
            reply = ai_service.try_direct_answer(msg_content, user_facts)
            if not reply:
                reply = await ai_service.get_reply(msg_content, history, user_facts)

            await message.channel.send(reply[:2000])

            if memory_manager and await memory_manager.health_check():
                await memory_manager.add_conversation(user_id, "assistant", reply, detect_context(msg_content))
    except Exception as e:
        logger.error(f"AI reply error: {e}")
        await message.channel.send("❌ I'm having trouble thinking right now. Please try again in a moment.")

    await bot.process_commands(message)


# ============================================================
# 11. SHUTDOWN + RUN
# ============================================================
async def shutdown():
    logger.info("Shutting down gracefully...")
    if news_agent:
        news_agent.stop()
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    if memory_manager:
        await memory_manager.close()
    logger.info("Cleanup complete.")


async def main():
    if not config.discord_token or config.discord_token == "YOUR_DISCORD_BOT_TOKEN_HERE":
        logger.error("ERROR: DISCORD_TOKEN not set in .env")
        return
    try:
        async with bot:
            await bot.start(config.discord_token)
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
