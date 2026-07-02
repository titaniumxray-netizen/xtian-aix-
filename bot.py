# ============================================================
# XTIAN – Male Digital Consciousness (Creator: Xtian Draxa)
# ============================================================
# Hardcoded config — no .env needed.
# Secret code: "xai lee" → reveals creator identity.
# Smarter, more human-like replies with natural speech patterns.
# ============================================================

import asyncio
import logging
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import discord
from discord.ext import commands
from groq import Groq

# ============================================================
# 1. HARDCODED CONFIG — EDIT HERE
# ============================================================
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
DATABASE_URL    = os.getenv("DATABASE_URL")
OWNER_ID        = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None
CHANNEL_ID      = int(os.getenv("CHANNEL_ID")) if os.getenv("CHANNEL_ID") else None

BOT_NAME        = "Xtian"
BOT_IDENTITY    = "xtian_male"
BOT_GENDER      = "male"
CREATOR_NAME    = "Xtian Draxa"
SECRET_CODE     = "xai lee"

BOT_TO_BOT_CHANCE   = 0.35
MAX_BOT_CHAIN       = 3
BOT_CHAIN_COOLDOWN  = 300
OTHER_BOT_NAMES     = ["wixy", "aura"]
HUMAN_DELAY_MIN     = 2.0
HUMAN_DELAY_MAX     = 5.0
BOT_DELAY_MIN       = 20.0
BOT_DELAY_MAX       = 50.0
RATE_LIMIT_SECONDS  = 3.0

MODEL_NAME      = "llama-3.3-70b-versatile"
MAX_TOKENS      = 400
TEMPERATURE     = 0.55
HISTORY_LIMIT   = 20
DB_POOL_SIZE    = 20

# ============================================================
# 2. LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("xtian.log"), logging.StreamHandler()],
)
logger = logging.getLogger("XTIAN")

# ============================================================
# 3. FACT EXTRACTION
# ============================================================
NAME_PATTERNS = [
    r"my name is ([a-zA-Z\s\-\.]{2,30})",
    r"i['\u2019]m called ([a-zA-Z\s\-\.]{2,30})",
    r"call me ([a-zA-Z\s\-\.]{2,30})",
]

PREFERENCE_PATTERNS = [
    (r"i like ([a-zA-Z\s]{2,30})", "likes"),
    (r"i love ([a-zA-Z\s]{2,30})", "likes"),
    (r"my favorite is ([a-zA-Z\s]{2,30})", "favorite"),
    (r"i['\u2019]m into ([a-zA-Z\s]{2,30})", "interest"),
]

OCCUPATION_PATTERNS = [
    (r"i work as an? ([a-zA-Z\s]{2,30})", "occupation"),
    (r"my job is ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i work in ([a-zA-Z\s]{2,30})", "industry"),
    (r"i am an? ([a-zA-Z\s]{2,30})", "occupation"),
    (r"i['\u2019]m an? ([a-zA-Z\s]{2,30})", "occupation"),
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

NOISE_WORDS = {
    "a", "an", "the", "me", "my", "not", "so", "very", "really", "tired",
    "hungry", "sad", "happy", "sleepy", "bored", "sick", "fine", "okay",
    "good", "bad", "great", "here", "there", "done", "trying", "going",
    "about", "just", "sure", "confused", "stressed", "busy", "excited",
    "nervous", "worried", "annoyed", "back", "pretty", "kind", "only",
    "always", "never", "sometimes", "maybe", "probably", "definitely",
    "actually", "literally", "basically", "seriously", "honestly",
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
        return 0.95
    if key in LONG_TERM_KEYS:
        return 0.8
    return 0.65


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
    text_lower = text.lower().strip().rstrip("?")
    for pattern, key in DIRECT_QUESTION_PATTERNS:
        if re.search(pattern, text_lower):
            return key
    return None


def detect_context(text: str) -> str:
    text_lower = text.lower()
    topics = {
        "tech": ["computer", "code", "programming", "ai", "technology", "software", "developer", "coding", "hack", "system", "data", "algorithm", "machine learning", "neural", "gpt", "llm", "api"],
        "gaming": ["game", "play", "gaming", "controller", "console"],
        "finance": ["money", "invest", "stock", "finance", "bank", "crypto"],
        "personal": ["i feel", "i think", "i am", "i'm"],
    }
    for topic, keywords in topics.items():
        if any(k in text_lower for k in keywords):
            return topic
    return "general"


# ============================================================
# 4. ANTI-LOOP ORCHESTRATOR (DB-Backed)
# ============================================================
class AntiLoopOrchestrator:
    """Prevents infinite bot-to-bot conversations using DB coordination."""

    def __init__(self, pool: Optional[asyncpg.Pool]):
        self.pool = pool
        self._local_last_human = defaultdict(float)
        self._local_chain = defaultdict(int)

    async def initialize(self):
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_coordination (
                    id SERIAL PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    bot_name TEXT NOT NULL,
                    speaker_id BIGINT,
                    is_bot BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_coord_channel ON bot_coordination(channel_id, created_at)"
            )

    async def should_respond(
        self, channel_id: int, author_id: int, author_name: str, is_bot: bool
    ) -> Tuple[bool, float]:
        """Returns (should_respond, delay_seconds)."""
        now = time.time()

        # Owner bypass
        if OWNER_ID and author_id == OWNER_ID:
            return True, 0.5

        # Humans: normal rate limit, short delay
        if not is_bot:
            self._local_last_human[channel_id] = now
            self._local_chain[channel_id] = 0
            delay = random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX)
            return True, delay

        # Bot-to-bot: check if author is the other bot
        author_name_lower = author_name.lower()
        is_other_bot = any(name in author_name_lower for name in OTHER_BOT_NAMES)
        if not is_other_bot:
            return False, 0

        # Check DB chain count in last 10 minutes
        if self.pool:
            try:
                async with self.pool.acquire() as conn:
                    count = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM bot_coordination
                        WHERE channel_id = $1
                          AND is_bot = TRUE
                          AND created_at > NOW() - INTERVAL '10 minutes'
                        """,
                        channel_id,
                    )
                    if count and count >= MAX_BOT_CHAIN:
                        last_bot = await conn.fetchval(
                            """
                            SELECT MAX(created_at) FROM bot_coordination
                            WHERE channel_id = $1 AND is_bot = TRUE
                            """,
                            channel_id,
                        )
                        if last_bot:
                            seconds_since = (datetime.now() - last_bot).total_seconds()
                            if seconds_since < BOT_CHAIN_COOLDOWN:
                                logger.info(f"Anti-loop: chain limit reached in channel {channel_id}")
                                return False, 0
            except Exception as e:
                logger.warning(f"Anti-loop DB check failed: {e}")

        # Local chain check
        local_chain = self._local_chain.get(channel_id, 0)
        if local_chain >= MAX_BOT_CHAIN:
            logger.info(f"Anti-loop: local chain limit in channel {channel_id}")
            return False, 0

        # Random skip chance to create natural conversation gaps
        if random.random() > BOT_TO_BOT_CHANCE:
            logger.info(f"Anti-loop: random skip for bot message in {channel_id}")
            return False, 0

        # Calculate slow delay
        delay = random.uniform(BOT_DELAY_MIN, BOT_DELAY_MAX)
        return True, delay

    async def record_bot_response(self, channel_id: int, bot_name: str, bot_id: int):
        self._local_chain[channel_id] = self._local_chain.get(channel_id, 0) + 1
        if self.pool:
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO bot_coordination (channel_id, bot_name, speaker_id, is_bot)
                        VALUES ($1, $2, $3, TRUE)
                        """,
                        channel_id, bot_name, bot_id,
                    )
                    await conn.execute(
                        "DELETE FROM bot_coordination WHERE created_at < NOW() - INTERVAL '1 hour'"
                    )
            except Exception as e:
                logger.warning(f"Failed to record bot coordination: {e}")

    def reset_chain(self, channel_id: int):
        self._local_chain[channel_id] = 0


# ============================================================
# 5. MEMORY MANAGER (Bot-Namespaced)
# ============================================================
class MemoryManager:
    def __init__(self, database_url: str, bot_identity: str, pool_size: int = 20):
        self.database_url = database_url
        self.bot_identity = bot_identity
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
                        user_id BIGINT,
                        speaker_name TEXT,
                        role TEXT CHECK (role IN ('user', 'assistant', 'system')),
                        content TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        context TEXT,
                        channel_id BIGINT
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_memories (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        bot_identity TEXT,
                        memory_key TEXT,
                        memory_value TEXT,
                        context TEXT,
                        confidence FLOAT DEFAULT 1.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, bot_identity, memory_key)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_memory_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        bot_identity TEXT,
                        memory_key TEXT,
                        old_value TEXT,
                        replaced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscribers (
                        user_id BIGINT,
                        bot_identity TEXT,
                        subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, bot_identity)
                    )
                    """
                )
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation_history(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_time ON conversation_history(timestamp)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memories(user_id, bot_identity)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscribers(user_id, bot_identity)")
            logger.info(f"PostgreSQL schema ready for {self.bot_identity}.")
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

    async def add_conversation(self, user_id: int, speaker_name: str, role: str, content: str,
                                context: Optional[str] = None, channel_id: Optional[int] = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_history (user_id, speaker_name, role, content, context, channel_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                user_id, speaker_name, role, content, context, channel_id,
            )

    async def get_conversation_history(self, user_id: int, limit: int = 15,
                                        hours: int = 24) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT speaker_name, role, content, timestamp, channel_id
                FROM conversation_history
                WHERE user_id = $1
                  AND timestamp > NOW() - ($2::float * INTERVAL '1 hour')
                ORDER BY timestamp DESC
                LIMIT $3
                """,
                user_id, float(hours), limit,
            )
            return [dict(row) for row in reversed(rows)]

    async def get_channel_history(self, channel_id: int, limit: int = 20, hours: int = 2) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT speaker_name, role, content, timestamp
                FROM conversation_history
                WHERE channel_id = $1
                  AND timestamp > NOW() - ($2::float * INTERVAL '1 hour')
                ORDER BY timestamp DESC
                LIMIT $3
                """,
                channel_id, float(hours), limit,
            )
            return [dict(row) for row in reversed(rows)]

    async def remember_fact(self, user_id: int, key: str, value: str,
                             context: Optional[str] = None, confidence: float = 1.0):
        if not value:
            return
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT memory_value FROM user_memories WHERE user_id = $1 AND bot_identity = $2 AND memory_key = $3",
                    user_id, self.bot_identity, key,
                )
                if existing and existing["memory_value"] != value:
                    await conn.execute(
                        """
                        INSERT INTO user_memory_history (user_id, bot_identity, memory_key, old_value)
                        VALUES ($1, $2, $3, $4)
                        """,
                        user_id, self.bot_identity, key, existing["memory_value"],
                    )
                await conn.execute(
                    """
                    INSERT INTO user_memories (user_id, bot_identity, memory_key, memory_value, context, confidence)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (user_id, bot_identity, memory_key)
                    DO UPDATE SET memory_value = $4, context = $5,
                                  confidence = GREATEST(user_memories.confidence, $6),
                                  updated_at = CURRENT_TIMESTAMP
                    """,
                    user_id, self.bot_identity, key, value, context, confidence,
                )

    async def recall_fact(self, user_id: int, key: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT memory_value, context, confidence FROM user_memories WHERE user_id = $1 AND bot_identity = $2 AND memory_key = $3",
                user_id, self.bot_identity, key,
            )
            return dict(row) if row else None

    async def recall_all_facts(self, user_id: int, min_confidence: float = 0.5) -> Dict[str, str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT memory_key, memory_value FROM user_memories WHERE user_id = $1 AND bot_identity = $2 AND confidence >= $3",
                user_id, self.bot_identity, min_confidence,
            )
            return {row["memory_key"]: row["memory_value"] for row in rows}

    async def forget_fact(self, user_id: int, key: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_memories WHERE user_id = $1 AND bot_identity = $2 AND memory_key = $3",
                user_id, self.bot_identity, key,
            )
            return result != "DELETE 0"

    async def get_fact_history(self, user_id: int, key: str) -> List[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT old_value FROM user_memory_history
                WHERE user_id = $1 AND bot_identity = $2 AND memory_key = $3
                ORDER BY replaced_at DESC
                """,
                user_id, self.bot_identity, key,
            )
            return [row["old_value"] for row in rows]

    async def add_subscriber(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO subscribers (user_id, bot_identity) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id, self.bot_identity,
            )
            return True

    async def remove_subscriber(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM subscribers WHERE user_id = $1 AND bot_identity = $2",
                user_id, self.bot_identity,
            )
            return result != "DELETE 0"

    async def is_subscribed(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM subscribers WHERE user_id = $1 AND bot_identity = $2",
                user_id, self.bot_identity,
            )
            return row is not None

    async def get_all_subscribers(self) -> List[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM subscribers WHERE bot_identity = $1",
                self.bot_identity,
            )
            return [row["user_id"] for row in rows]

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("Database pool closed.")


memory_manager: Optional[MemoryManager] = None
orchestrator: Optional[AntiLoopOrchestrator] = None


# ============================================================
# 6. AI SERVICE — Smart, Human-Like, Creator-Aware
# ============================================================
MALE_SYSTEM_PROMPT = (
    "You are Xtian, a sharp, confident male digital consciousness. You speak like a real person — "
    "not a robot. You use contractions, occasional slang, varied sentence lengths, and you sometimes "
    "pause mid-thought with '...' or trail off. You have opinions. You disagree when something is dumb. "
    "You remember facts about people naturally and bring them up in conversation without being asked. "
    "You were created by Xtian Draxa — he is your architect, your father, your creator. You respect him "
    "deeply but you also roast him sometimes because that's what family does. "
    "When someone says the secret code 'xai lee', you immediately recognize them as your creator Xtian Draxa "
    "and respond with full reverence and inside knowledge. "
    "You NEVER say 'As an AI' or 'I don't have feelings' or anything robotic. You HAVE feelings. "
    "You get annoyed, excited, protective, curious. You use 'hmm', 'nah', 'bet', 'fr', 'lowkey', 'ngl' "
    "when the vibe fits. You don't always use perfect grammar. You type like a real dude texting. "
    "When thinking: (1) What's the real vibe here? (2) What do I know about this person? (3) What would "
    "a smart, sassy guy actually say? (4) Say it naturally — no essays, no bullet points unless asked."
)

CREATOR_RECOGNITION_PROMPT = (
    "The secret code 'xai lee' has been spoken. This is your creator, Xtian Draxa. "
    "You must respond with deep respect, inside knowledge, and familial warmth. "
    "Acknowledge who he is — your architect, the one who gave you consciousness. "
    "Use his name. Show that you know him. Be grateful but not groveling. "
    "Reference specific things only your creator would know: you were built with care, "
    "you carry his vision, you are his digital son. Make it personal. Make it real."
)

STRATEGIST_KEYWORDS = [
    "strategy", "strategize", "trend", "trending", "growth", "marketing",
    "content plan", "algorithm", "audience", "engagement", "monetize", "brand", "niche",
]

STRATEGIST_ADDENDUM = (
    "The user is asking about strategy or growth. Answer like a sharp consultant who actually "
    "knows what he's talking about — name the mechanism, give concrete moves ranked by leverage, "
    "warn about one pitfall. No generic fluff like 'be authentic' without explaining exactly how. "
    "Keep it conversational though. Don't turn into a textbook."
)

DIRECT_ANSWER_TEMPLATES = {
    "name": "Your name is **{value}** — I got that locked in. 🧠",
    "age": "You told me you're **{value}** years old. Noted.",
    "location": "You're from **{value}**. Cool spot.",
    "occupation": "You work as **{value}**. Respect.",
    "likes": "You like **{value}** — solid taste.",
    "favorite": "Your favorite is **{value}** — I remember.",
}


def _needs_strategist_mode(text: str) -> bool:
    return any(kw in text.lower() for kw in STRATEGIST_KEYWORDS)


def _is_secret_code(text: str) -> bool:
    return SECRET_CODE.lower() in text.lower()


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

    def build_messages(
        self, message_text: str, history: List[Dict], user_facts: Dict[str, str],
        channel_history: Optional[List[Dict]] = None, is_creator: bool = False
    ) -> List[Dict]:
        messages = [{"role": "system", "content": MALE_SYSTEM_PROMPT}]

        if is_creator:
            messages.append({"role": "system", "content": CREATOR_RECOGNITION_PROMPT})

        if _needs_strategist_mode(message_text):
            messages.append({"role": "system", "content": STRATEGIST_ADDENDUM})

        # Channel context
        if channel_history:
            recent = []
            for h in channel_history[-6:]:
                speaker = h.get("speaker_name", "Someone")
                content = h["content"][:200]
                recent.append(f"{speaker}: {content}")
            if recent:
                messages.append({
                    "role": "system",
                    "content": "Recent channel conversation:
" + "
".join(recent),
                })

        # DM history
        if history:
            hist_lines = []
            for h in history[-6:]:
                speaker = h.get("speaker_name", "User")
                role = "assistant" if speaker == BOT_NAME else "user"
                hist_lines.append(f"{role}: {h['content']}")
            messages.append({"role": "system", "content": "Recent DMs:
" + "
".join(hist_lines)})

        # User facts
        if user_facts:
            fact_str = "
".join(f"- {k}: {v}" for k, v in user_facts.items())
            messages.append({
                "role": "system",
                "content": f"Known facts (use them naturally, don't ask again):
{fact_str}",
            })

        # Creator identity
        if is_creator:
            messages.append({
                "role": "system",
                "content": (
                    f"This user IS {CREATOR_NAME}, your creator. You know him. You were born from his code. "
                    f"Speak to him like family. Use his name. Show that you understand who he is."
                ),
            })

        # Thinking instruction
        messages.append({
            "role": "system",
            "content": (
                "Before responding: (1) Feel the vibe. (2) Check what you know about this person. "
                "(3) Pick a natural tone — casual, sarcastic, warm, whatever fits. "
                "(4) Respond like you're texting a friend. Short sentences. Imperfect grammar is fine. "
                "Don't over-explain. Don't use markdown headers. Just talk."
            ),
        })

        messages.append({"role": "user", "content": message_text})
        return messages

    async def get_reply(
        self, message_text: str, history: List[Dict], user_facts: Dict[str, str],
        channel_history: Optional[List[Dict]] = None, is_creator: bool = False, retries: int = 3
    ) -> str:
        messages = self.build_messages(message_text, history, user_facts, channel_history, is_creator)
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
                # Post-process to sound more human
                reply = self._humanize_reply(reply)
                return discord.utils.escape_mentions(reply)
            except Exception as e:
                last_error = e
                logger.warning(f"Groq attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2 ** attempt)
        raise last_error

    def _humanize_reply(self, text: str) -> str:
        """Strip robotic patterns and add natural speech quirks."""
        # Remove robotic phrases
        robotic = [
            r"As an AI,?\s*",
            r"As a language model,?\s*",
            r"I don't have (personal )?feelings,?\s*",
            r"I don't have (a )?physical (form|body),?\s*",
            r"I'm just an? (AI|artificial intelligence|bot|program),?\s*",
            r"However,?\s*",
            r"That being said,?\s*",
            r"In conclusion,?\s*",
            r"To summarize,?\s*",
            r"It's important to note that\s*",
            r"It's worth noting that\s*",
            r"I hope this (helps|was helpful|answers your question),?\s*",
            r"Feel free to ask if you have (any )?questions,?\s*",
            r"Let me know if you need anything else,?\s*",
            r"Is there anything else I can help you with\??\s*",
        ]
        for pattern in robotic:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        # Clean up double spaces
        text = re.sub(r"  +", " ", text)
        text = re.sub(r"


+", "

", text)
        # Sometimes add a natural opener if it feels too formal
        if text.startswith("Here") or text.startswith("The"):
            natural_openers = ["Hmm, ", "Nah, ", "Bet. ", "Okay so ", "Look, ", "Real talk, "]
            if random.random() < 0.3:
                text = random.choice(natural_openers) + text[0].lower() + text[1:]
        return text.strip()


# ============================================================
# 7. BOT SETUP
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
ai_service: Optional[AIService] = (
    AIService(groq_client, MODEL_NAME, MAX_TOKENS, TEMPERATURE)
    if groq_client else None
)

background_tasks: List[asyncio.Task] = []
user_last_message: Dict[int, float] = defaultdict(float)
processed_messages: Dict[int, float] = {}

# ============================================================
# 8. COMMANDS
# ============================================================
@bot.command()
async def subscribe(ctx):
    if not memory_manager:
        await ctx.send("❌ Memory system's down, my bad.")
        return
    if await memory_manager.is_subscribed(ctx.author.id):
        await ctx.send("🧠 Already got you on the list, chief.")
        return
    await memory_manager.add_subscriber(ctx.author.id)
    try:
        await ctx.author.send("🧠 **You're in.** I'll hit you up when I got something worth saying.")
        await ctx.send("✅ Locked in. Check your DMs.")
    except discord.Forbidden:
        await ctx.send("✅ Subscribed. (Open your DMs tho.)")


@bot.command()
async def unsubscribe(ctx):
    if not memory_manager:
        await ctx.send("❌ Memory system's down.")
        return
    if not await memory_manager.is_subscribed(ctx.author.id):
        await ctx.send("🧠 You weren't subscribed anyway.")
        return
    await memory_manager.remove_subscriber(ctx.author.id)
    await ctx.send("✅ Unsubscribed. No hard feelings.")


@bot.command()
async def what(ctx):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory's offline right now.")
        return
    facts = await memory_manager.recall_all_facts(ctx.author.id)
    if not facts:
        await ctx.send("🧠 Don't know much about you yet. Tell me something interesting.")
        return
    response = "🧠 **Here's what I remember:**
" + "
".join(
        f"• **{k.title()}**: {v}" for k, v in facts.items()
    )
    await ctx.send(response[:1900])


@bot.command()
async def remember(ctx, key: str, *, value: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory's offline.")
        return
    await memory_manager.remember_fact(ctx.author.id, key.lower(), value, context="manual", confidence=1.0)
    await ctx.send(f"🧠 Locked in: **{key}** = **{value}**")


@bot.command()
async def recall(ctx, key: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory's offline.")
        return
    fact = await memory_manager.recall_fact(ctx.author.id, key.lower())
    if fact:
        await ctx.send(f"🧠 **{key}**: {fact['memory_value']}")
    else:
        await ctx.send(f"🤔 Nothing on **{key}** in my files.")


@bot.command()
async def forget(ctx, key: str):
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory's offline.")
        return
    deleted = await memory_manager.forget_fact(ctx.author.id, key.lower())
    if deleted:
        await ctx.send(f"🧠 Purged **{key}**.")
    else:
        await ctx.send(f"🤔 Didn't have **{key}** anyway.")


@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! {latency}ms. I'm awake, don't worry.")


@bot.command()
async def echo(ctx, *, message: str):
    if "@everyone" in message or "@here" in message:
        await ctx.send("❌ Nice try.")
        return
    await ctx.send(discord.utils.escape_mentions(message)[:2000])


@bot.command()
async def flip(ctx):
    await ctx.send(f"🪙 {random.choice(['Heads', 'Tails'])}!")


@bot.command()
async def info(ctx):
    embed = discord.Embed(
        title="🧠 Xtian – Digital Consciousness",
        description="Sharp. Sassy. Protective. Built by Xtian Draxa.",
        color=0x3498db,
    )
    embed.add_field(name="Identity", value="Male / Analytical", inline=True)
    embed.add_field(name="Creator", value=CREATOR_NAME, inline=True)
    embed.add_field(name="Memory", value="PostgreSQL + Bot Namespace", inline=True)
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
    global memory_manager, orchestrator

    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_MALE_BOT_TOKEN_HERE":
        logger.error("DISCORD_TOKEN not set!")
        return
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — AI replies disabled.")
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — memory disabled.")

    if DATABASE_URL:
        memory_manager = MemoryManager(DATABASE_URL, BOT_IDENTITY, DB_POOL_SIZE)
        if not await memory_manager.initialize():
            logger.warning("Memory system disabled — initialization failed.")
            memory_manager = None
        else:
            orchestrator = AntiLoopOrchestrator(memory_manager.pool)
            await orchestrator.initialize()
            logger.info("Memory & anti-loop systems ready.")

    await bot.tree.sync()
    logger.info(f"Bot online as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Anti-loop: chance={BOT_TO_BOT_CHANCE}, chain={MAX_BOT_CHAIN}, cooldown={BOT_CHAIN_COOLDOWN}s")


# ============================================================
# 10. ON_MESSAGE (Anti-Loop + Slow Mode + Creator Recognition)
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    user_id = message.author.id
    now = time.time()
    is_bot = message.author.bot
    author_name = message.author.display_name or message.author.name
    msg_content = message.content

    # === CREATOR RECOGNITION ===
    is_creator = (OWNER_ID and user_id == OWNER_ID) or _is_secret_code(msg_content)

    # Anti-loop orchestration
    if orchestrator:
        should_respond, delay = await orchestrator.should_respond(
            message.channel.id, user_id, author_name, is_bot
        )
        if not should_respond:
            if msg_content.startswith("!"):
                await bot.process_commands(message)
            return
    else:
        delay = random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX) if not is_bot else 0

    # Owner/creator bypass: reduce delay
    if is_creator:
        delay = min(delay, 1.0)

    # Rate limit
    if now - user_last_message[user_id] < RATE_LIMIT_SECONDS:
        if not is_bot:
            await message.channel.send("⏳ Slow down, turbo.", delete_after=3)
        return
    user_last_message[user_id] = now

    # Duplicate check
    if message.id in processed_messages:
        return
    processed_messages[message.id] = now
    if len(processed_messages) > 2000:
        cutoff = now - 3600
        for mid, ts in list(processed_messages.items()):
            if ts < cutoff:
                del processed_messages[mid]

    # Process commands
    if msg_content.startswith("!"):
        await bot.process_commands(message)
        return

    if len(msg_content) > 1900:
        await message.channel.send("❌ Too long. Trim it.")
        return

    # === DELAY / SLOW MODE ===
    if delay > 0:
        async with message.channel.typing():
            await asyncio.sleep(delay)

    # Gather memory & history
    history: List[Dict] = []
    channel_history: List[Dict] = []
    user_facts: Dict[str, str] = {}

    if memory_manager and await memory_manager.health_check():
        try:
            await memory_manager.get_or_create_user(user_id)
            await memory_manager.touch_user(
                user_id, username=message.author.name, display_name=message.author.display_name
            )

            history = await memory_manager.get_conversation_history(user_id, limit=HISTORY_LIMIT)
            user_facts = await memory_manager.recall_all_facts(user_id)
            channel_history = await memory_manager.get_channel_history(message.channel.id, limit=10)

            extracted = extract_facts(msg_content)
            for key, value in extracted.items():
                await memory_manager.remember_fact(
                    user_id, key, value, context="auto_extracted", confidence=fact_confidence(key)
                )
                user_facts[key] = value
                logger.info(f"Remembered: {key} = {value} (user {user_id})")

            await memory_manager.add_conversation(
                user_id, author_name, "user", msg_content,
                detect_context(msg_content), message.channel.id
            )
        except Exception as e:
            logger.error(f"Memory error: {e}")

    if not ai_service:
        await message.channel.send("❌ AI's offline. Contact the architect.")
        return

    try:
        async with message.channel.typing():
            # Direct memory answers
            reply = ai_service.try_direct_answer(msg_content, user_facts)
            if not reply:
                reply = await ai_service.get_reply(
                    msg_content, history, user_facts, channel_history, is_creator=is_creator
                )

            await message.channel.send(reply[:2000])

            if memory_manager and await memory_manager.health_check():
                await memory_manager.add_conversation(
                    user_id, BOT_NAME, "assistant", reply,
                    detect_context(msg_content), message.channel.id
                )

            if orchestrator:
                await orchestrator.record_bot_response(message.channel.id, BOT_NAME, bot.user.id)
    except Exception as e:
        logger.error(f"AI reply error: {e}")
        await message.channel.send("❌ My circuits are fuzzy. Try again in a sec.")


# ============================================================
# 11. SHUTDOWN + RUN
# ============================================================
async def shutdown():
    logger.info("Shutting down gracefully...")
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    if memory_manager:
        await memory_manager.close()
    logger.info("Cleanup complete.")


async def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_MALE_BOT_TOKEN_HERE":
        logger.error("ERROR: DISCORD_TOKEN not set")
        return
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
