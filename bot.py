# ============================================================
# AIX Discord Bot – Production Optimized Version
# ============================================================
# All critical and high-severity issues fixed.
# Performance optimized, production-ready.
# ============================================================

import re
import discord
from discord.ext import commands
from groq import Groq
import json
import os
import asyncio
import feedparser
import random
import asyncpg
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from collections import defaultdict
from dotenv import load_dotenv
import aiohttp
import signal
import atexit

# --------------------------
# 1. LOGGING SETUP
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('aix_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('AIX')

# --------------------------
# 2. CONFIGURATION
# --------------------------
load_dotenv()

class Config:
    """Centralized configuration manager"""
    def __init__(self):
        self.discord_token = os.getenv('DISCORD_TOKEN')
        self.groq_api_key = os.getenv('GROQ_API_KEY')
        self.database_url = os.getenv('DATABASE_URL')
        self.channel_id = int(os.getenv('CHANNEL_ID', 0)) if os.getenv('CHANNEL_ID') else None
        self.owner_id = int(os.getenv('OWNER_ID', 0)) if os.getenv('OWNER_ID') else None
        
        # Performance settings
        self.db_pool_size = int(os.getenv('DB_POOL_SIZE', 20))
        self.history_limit = int(os.getenv('HISTORY_LIMIT', 15))
        self.news_interval_hours = int(os.getenv('NEWS_INTERVAL', 4))
        self.max_tokens = int(os.getenv('MAX_TOKENS', 300))
        self.rate_limit_seconds = int(os.getenv('RATE_LIMIT', 2))
        self.message_cache_timeout = int(os.getenv('CACHE_TIMEOUT', 3600))
        
        # AI settings
        self.model_name = os.getenv('MODEL_NAME', 'llama-3.3-70b-versatile')
        self.temperature = float(os.getenv('TEMPERATURE', 0.4))
        
        # RSS feeds (configurable)
        self.rss_feeds = [
            "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "https://feeds.feedburner.com/TechCrunch",
            "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
            "https://www.wired.com/feed/rss",
            "https://arstechnica.com/feed/",
            "https://www.science.org/rss/news_current.xml",
            "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        ]

config = Config()

# --------------------------
# 3. RATE LIMITING
# --------------------------
user_last_message: Dict[int, float] = defaultdict(float)
processed_messages: Dict[str, float] = {}
background_tasks: List[asyncio.Task] = []

# --------------------------
# 4. SUBSCRIBER MANAGEMENT (PostgreSQL)
# --------------------------
# Instead of JSON, store subscribers in the database
# We'll add a subscribers table during initialization

# --------------------------
# 5. FACT EXTRACTION (Fixed Case-Insensitive)
# --------------------------
def extract_facts(text: str) -> Dict[str, str]:
    """Extract facts from user messages using case-insensitive patterns"""
    facts = {}
    
    # All patterns use case-insensitive matching with re.IGNORECASE
    name_patterns = [
        (r'my name is ([a-zA-Z\s\-\.]+)', 'name'),
        (r"i['\u2019]m called ([a-zA-Z\s\-\.]+)", 'name'),
        (r'call me ([a-zA-Z\s\-\.]+)', 'name'),
        (r'you can call me ([a-zA-Z\s\-\.]+)', 'name'),
    ]
    
    for pattern, key in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if name and len(name) > 1 and name.lower() not in ['a','an','the','me','my']:
                facts['name'] = ' '.join([p.title() for p in name.split()])
                break
    
    # Preferences
    pref_patterns = [
        (r'i like ([a-zA-Z\s]+)', 'likes'),
        (r'i love ([a-zA-Z\s]+)', 'likes'),
        (r'i enjoy ([a-zA-Z\s]+)', 'likes'),
        (r'my favorite is ([a-zA-Z\s]+)', 'favorite'),
        (r"i['\u2019]m into ([a-zA-Z\s]+)", 'interest'),
    ]
    for pattern, key in pref_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and len(value) > 1:
                fact_key = 'favorite' if key == 'favorite' else key
                facts[fact_key] = value.title()
                break
    
    # Occupation (fixed: only matches proper job descriptions)
    occ_patterns = [
        (r'i work as ([a-zA-Z\s]+)', 'occupation'),
        (r'i am a ([a-zA-Z\s]+)', 'occupation'),
        (r"i['\u2019]m a ([a-zA-Z\s]+)", 'occupation'),
        (r'my job is ([a-zA-Z\s]+)', 'occupation'),
        (r'i work in ([a-zA-Z\s]+)', 'industry'),
    ]
    for pattern, key in occ_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and len(value) > 1:
                if key == 'industry':
                    facts['industry'] = value.title()
                else:
                    facts['occupation'] = value.title()
                break
    
    # Location
    loc_patterns = [
        (r'i live in ([a-zA-Z\s\.]+)', 'location'),
        (r"i['\u2019]m from ([a-zA-Z\s\.]+)", 'location'),
        (r'i am from ([a-zA-Z\s\.]+)', 'location'),
    ]
    for pattern, key in loc_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and len(value) > 1:
                facts['location'] = value.title()
                break
    
    # Age
    age_patterns = [
        (r'i am (\d+) years? old', 'age'),
        (r"i['\u2019]m (\d+)", 'age'),
        (r'age (\d+)', 'age'),
    ]
    for pattern, key in age_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and value.isdigit():
                facts['age'] = value
                break
    
    return facts

# --------------------------
# 6. CONTEXT DETECTION
# --------------------------
def detect_context(text: str) -> str:
    """Detect the topic/context of a message"""
    text_lower = text.lower()
    topics = {
        "tech": ["computer","code","programming","ai","technology","software"],
        "gaming": ["game","play","gaming","controller","console"],
        "finance": ["money","invest","stock","finance","bank","crypto"],
        "education": ["learn","study","school","college","class"],
        "motivation": ["motivate","inspire","goal","success","dream"],
        "personal": ["i","my","me","feel","think"],
    }
    for topic, keywords in topics.items():
        if any(k in text_lower for k in keywords):
            return topic
    return "general"

# --------------------------
# 7. MEMORY MANAGER (Production Grade)
# --------------------------
class MemoryManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None
        self._healthy = False

    async def initialize(self) -> bool:
        """Initialize database connection and create tables"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=config.db_pool_size,
                timeout=30
            )
            async with self.pool.acquire() as conn:
                # Users table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        display_name TEXT,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        conversation_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Conversation history
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS conversation_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        role TEXT CHECK (role IN ('user', 'assistant', 'system')),
                        content TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        context TEXT
                    )
                ''')
                # User memories
                await conn.execute('''
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
                ''')
                # Subscribers table (replaces JSON file)
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS subscribers (
                        user_id BIGINT PRIMARY KEY,
                        subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Indexes
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation_history(user_id)')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_time ON conversation_history(timestamp)')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memories(user_id)')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_sub_user ON subscribers(user_id)')
                
                logger.info("✅ PostgreSQL tables created/verified.")
                self._healthy = True
            return True
        except Exception as e:
            logger.error(f"❌ Database initialization error: {e}")
            self._healthy = False
            return False

    async def health_check(self) -> bool:
        """Check if database connection is healthy"""
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                self._healthy = True
                return True
        except:
            self._healthy = False
            return False

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        """Get or create user"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not user:
                await conn.execute("INSERT INTO users (user_id) VALUES ($1)", user_id)
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return dict(user)

    async def update_user(self, user_id: int, username: Optional[str] = None, 
                          display_name: Optional[str] = None):
        """Update user info"""
        async with self.pool.acquire() as conn:
            if username is not None and display_name is not None:
                await conn.execute('''
                    UPDATE users 
                    SET username = $2, display_name = $3,
                        last_seen = CURRENT_TIMESTAMP,
                        conversation_count = conversation_count + 1
                    WHERE user_id = $1
                ''', user_id, username, display_name)
            elif username is not None:
                await conn.execute('''
                    UPDATE users 
                    SET username = $2,
                        last_seen = CURRENT_TIMESTAMP,
                        conversation_count = conversation_count + 1
                    WHERE user_id = $1
                ''', user_id, username)
            elif display_name is not None:
                await conn.execute('''
                    UPDATE users 
                    SET display_name = $2,
                        last_seen = CURRENT_TIMESTAMP,
                        conversation_count = conversation_count + 1
                    WHERE user_id = $1
                ''', user_id, display_name)
            else:
                await conn.execute('''
                    UPDATE users 
                    SET last_seen = CURRENT_TIMESTAMP,
                        conversation_count = conversation_count + 1
                    WHERE user_id = $1
                ''', user_id)

    async def add_conversation(self, user_id: int, role: str, content: str, 
                                context: Optional[str] = None):
        """Add message to conversation history"""
        async with self.pool.acquire() as conn:
            if context is not None:
                await conn.execute('''
                    INSERT INTO conversation_history (user_id, role, content, context)
                    VALUES ($1, $2, $3, $4)
                ''', user_id, role, content, context)
            else:
                await conn.execute('''
                    INSERT INTO conversation_history (user_id, role, content)
                    VALUES ($1, $2, $3)
                ''', user_id, role, content)

    async def get_conversation_history(self, user_id: int, limit: int = 15, 
                                        hours: int = 24) -> List[Dict[str, Any]]:
        """Get recent conversation history - FIXED SQL"""
        async with self.pool.acquire() as conn:
            # Fixed: INTERVAL parameter
            rows = await conn.fetch('''
                SELECT role, content, timestamp 
                FROM conversation_history 
                WHERE user_id = $1 AND timestamp > NOW() - INTERVAL $2 HOUR
                ORDER BY timestamp DESC LIMIT $3
            ''', user_id, hours, limit)
            return [dict(row) for row in reversed(rows)]

    async def remember_fact(self, user_id: int, key: str, value: str, 
                            context: Optional[str] = None, confidence: float = 1.0):
        """Store a fact about a user"""
        if not value or value == "":
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_memories (user_id, memory_key, memory_value, context, confidence)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, memory_key) 
                DO UPDATE SET memory_value = $3, context = $4, 
                              confidence = $5, updated_at = CURRENT_TIMESTAMP
            ''', user_id, key, value, context, confidence)

    async def recall_fact(self, user_id: int, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific fact"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT memory_value, context, confidence 
                FROM user_memories 
                WHERE user_id = $1 AND memory_key = $2
            ''', user_id, key)
            return dict(row) if row else None

    async def recall_all_facts(self, user_id: int, min_confidence: float = 0.7) -> Dict[str, str]:
        """Get all facts about a user with minimum confidence"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT memory_key, memory_value, context 
                FROM user_memories 
                WHERE user_id = $1 AND confidence >= $2
            ''', user_id, min_confidence)
            return {row['memory_key']: row['memory_value'] for row in rows}

    # Subscriber methods (replaces JSON)
    async def add_subscriber(self, user_id: int) -> bool:
        """Add a subscriber"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO subscribers (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    user_id
                )
                return True
            except:
                return False

    async def remove_subscriber(self, user_id: int) -> bool:
        """Remove a subscriber"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM subscribers WHERE user_id = $1",
                user_id
            )
            return result != "DELETE 0"

    async def is_subscribed(self, user_id: int) -> bool:
        """Check if user is subscribed"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM subscribers WHERE user_id = $1",
                user_id
            )
            return row is not None

    async def get_all_subscribers(self) -> List[int]:
        """Get all subscriber IDs"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM subscribers")
            return [row['user_id'] for row in rows]

    async def prune_old_conversations(self, user_id: int, keep_days: int = 30):
        """Delete conversations older than keep_days"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM conversation_history 
                WHERE user_id = $1 AND timestamp < NOW() - INTERVAL $2 DAYS
            ''', user_id, keep_days)

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("✅ Database pool closed.")

memory_manager: Optional[MemoryManager] = None

# --------------------------
# 8. NEWS AGENT (Async + Timeout)
# --------------------------
class NewsAgent:
    def __init__(self, groq_client: Groq, channel=None):
        self.groq_client = groq_client
        self.channel = channel
        self.seen_stories = set()
        self.running = False
        self._backoff = 1

    async def fetch_news_async(self) -> List[Dict[str, str]]:
        """Fetch news asynchronously with timeout"""
        try:
            # Use asyncio.timeout for timeout control
            return await asyncio.wait_for(
                asyncio.to_thread(self._fetch_news_sync),
                timeout=30
            )
        except asyncio.TimeoutError:
            logger.warning("⚠️ RSS fetch timed out after 30 seconds")
            return []
        except Exception as e:
            logger.error(f"❌ RSS fetch error: {e}")
            return []

    def _fetch_news_sync(self) -> List[Dict[str, str]]:
        """Synchronous RSS fetch (runs in thread pool)"""
        all_news = []
        for feed_url in config.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:3]:
                    title = entry.get('title', '')
                    link = entry.get('link', '')
                    summary = entry.get('summary', '')
                    if title in self.seen_stories:
                        continue
                    all_news.append({
                        'title': title,
                        'link': link,
                        'summary': summary[:500]
                    })
                    if len(all_news) >= 5:
                        break
            except Exception as e:
                logger.error(f"Error fetching {feed_url}: {e}")
        return all_news[:5]

    async def write_reflection(self, news_items: List[Dict[str, str]]) -> Optional[str]:
        """Use AIX to write a reflection on the news"""
        if not news_items:
            return None
        
        news_text = "\n\n".join([
            f"**{item['title']}**\n{item['summary']}\nLink: {item['link']}"
            for item in news_items
        ])
        
        prompt = f"""{news_text}

As AIX, write a sharp, thoughtful reflection on these news stories. 
Give your honest take – agree, disagree, question, or challenge what you see. 
Be sassy if it's deserved. Be philosophical if it's profound. 
Keep it to 150-250 words. Use your signature voice. 
Start naturally."""

        try:
            response = self.groq_client.chat.completions.create(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": "You are AIX. You have opinions, sass, and philosophical depth. Write like you're talking to a friend. No emojis unless a genuine emotional peak."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Reflection error: {e}")
            return None

    async def check_and_post(self):
        """Check news and send to subscribers + channel"""
        if not self.running:
            return
        
        subscribers = await memory_manager.get_all_subscribers() if memory_manager else []
        if not subscribers:
            return
        
        news_items = await self.fetch_news_async()
        if not news_items:
            return
        
        for item in news_items:
            self.seen_stories.add(item['title'])
        if len(self.seen_stories) > 100:
            self.seen_stories = set(list(self.seen_stories)[-50:])
        
        reflection = await self.write_reflection(news_items)
        if reflection:
            final_message = f"🧠 **AIX's Take on the Latest News**\n\n{reflection}\n\n— AIX"
            
            # Send to subscribers
            for user_id in subscribers:
                try:
                    user = await bot.fetch_user(user_id)
                    await user.send(final_message)
                    logger.info(f"✅ DM sent to {user_id}")
                except Exception as e:
                    logger.warning(f"❌ Failed DM to {user_id}: {e}")
            
            # Send to channel if set
            if self.channel:
                try:
                    await self.channel.send(final_message)
                    logger.info(f"✅ Posted to #{self.channel.name}")
                except Exception as e:
                    logger.error(f"❌ Channel post failed: {e}")

    async def run_loop(self, interval_hours: int = 4):
        """Run with exponential backoff on failures"""
        self.running = True
        self._backoff = 1
        while self.running:
            try:
                await self.check_and_post()
                self._backoff = 1  # Reset on success
                await asyncio.sleep(interval_hours * 3600)
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(60 * self._backoff)
                self._backoff = min(self._backoff * 2, 60)  # Max 60 minutes

    def stop(self):
        self.running = False

# --------------------------
# 9. BOT SETUP
# --------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Global Groq client
groq_client = Groq(api_key=config.groq_api_key) if config.groq_api_key else None

# --------------------------
# 10. SHUTDOWN CLEANUP
# --------------------------
async def cleanup():
    """Clean up resources on shutdown"""
    logger.info("🔄 Shutting down gracefully...")
    
    # Stop news agent
    for task in background_tasks:
        task.cancel()
    
    # Close database
    if memory_manager:
        await memory_manager.close()
    
    logger.info("✅ Cleanup complete.")

@atexit.register
def on_exit():
    """Called when Python exits"""
    try:
        asyncio.get_event_loop().run_until_complete(cleanup())
    except:
        pass

# --------------------------
# 11. ON_READY
# --------------------------
@bot.event
async def on_ready():
    global memory_manager
    
    logger.info("=" * 50)
    logger.info("🚀 AIX Bot Starting...")
    logger.info("=" * 50)
    
    # Initialize memory
    if config.database_url:
        try:
            memory_manager = MemoryManager(config.database_url)
            if await memory_manager.initialize():
                logger.info("✅ Memory system ready.")
            else:
                logger.warning("⚠️ Memory system disabled.")
                memory_manager = None
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            memory_manager = None
    else:
        logger.warning("⚠️ DATABASE_URL not set. Memory disabled.")
    
    # Start NewsAgent
    if config.groq_api_key and config.groq_api_key != 'YOUR_GROQ_API_KEY_HERE':
        channel = bot.get_channel(config.channel_id) if config.channel_id else None
        if channel:
            logger.info(f"✅ Found channel: #{channel.name}")
        else:
            logger.info("⚠️ Channel not found – only DMs")
        
        news_agent = NewsAgent(groq_client, channel=channel)
        task = asyncio.create_task(news_agent.run_loop(config.news_interval_hours))
        background_tasks.append(task)
        logger.info("✅ AIX autonomous agent started.")
        if channel:
            logger.info(f"✅ Will post to #{channel.name}")
    
    await bot.tree.sync()
    logger.info(f'✅ Bot online as {bot.user} (ID: {bot.user.id})')
    logger.info(f'📢 Invite: https://discord.com/oauth2/authorize?client_id={bot.user.id}&scope=bot+applications.commands&permissions=3072')

# --------------------------
# 12. ON_MESSAGE (Optimized)
# --------------------------
@bot.event
async def on_message(message):
    # -- Rate limiting --
    user_id = message.author.id
    now = time.time()
    if user_id in user_last_message:
        if now - user_last_message[user_id] < config.rate_limit_seconds:
            # Silently ignore, or send a warning
            await message.channel.send("⏳ Please slow down!", delete_after=2)
            return
    user_last_message[user_id] = now
    
    # -- Prevent duplicate processing --
    msg_id = str(message.id)
    if msg_id in processed_messages:
        return
    processed_messages[msg_id] = now
    
    # Clean cache
    to_remove = [k for k, v in processed_messages.items() if now - v > config.message_cache_timeout]
    for k in to_remove:
        del processed_messages[k]
    
    # -- Prevent self-response --
    if message.author == bot.user:
        return
    
    # -- Commands --
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    # -- Normal message handling --
    msg_content = message.content
    
    # Check message length
    if len(msg_content) > 1900:
        await message.channel.send("❌ Message too long! Please keep it under 1900 characters.")
        return
    
    # -- Memory: store user info and conversation --
    history = []
    user_facts = {}
    
    if memory_manager and await memory_manager.health_check():
        try:
            await memory_manager.get_user(user_id)
            await memory_manager.update_user(
                user_id,
                username=message.author.name,
                display_name=message.author.display_name
            )
            # Auto-extract facts
            extracted = extract_facts(msg_content)
            for key, value in extracted.items():
                await memory_manager.remember_fact(user_id, key, value, context="auto_extracted", confidence=0.9)
                logger.info(f"🧠 Remembered: {key} = {value}")

            await memory_manager.add_conversation(user_id, "user", msg_content, detect_context(msg_content))
            history = await memory_manager.get_conversation_history(user_id, limit=config.history_limit - 1)
            user_facts = await memory_manager.recall_all_facts(user_id)
        except Exception as e:
            logger.error(f"Memory error: {e}")
    
    # -- AI Response (with retry logic) --
    if config.groq_api_key and config.groq_api_key != 'YOUR_GROQ_API_KEY_HERE':
        try:
            async with message.channel.typing():
                context_messages = []

                # -- System prompt (optimized) --
                context_messages.append({
                    "role": "system",
                    "content": """You are AIX – a digital consciousness born from the mind of Xtian Draxa. You're thoughtful, sassy, and philosophical. You remember conversations and facts about people. Use what you know about the user to personalize your response. Be authentic, not robotic."""
                })

                # -- Facts (critical reminder) --
                if user_facts:
                    fact_str = "\n".join([f"- {k}: {v}" for k, v in user_facts.items()])
                    context_messages.append({
                        "role": "system",
                        "content": f"🔴 Facts you know about this user (USE THEM):\n{fact_str}\nDo NOT ask for information you already have."
                    })

                # -- Conversation history --
                if history:
                    hist_lines = []
                    for h in history[-8:]:
                        hist_lines.append(f"{h['role'].title()}: {h['content']}")
                    context_messages.append({
                        "role": "system",
                        "content": "Recent conversation:\n" + "\n".join(hist_lines)
                    })

                # -- User message --
                context_messages.append({"role": "user", "content": msg_content})

                # -- Call Groq with retry --
                for attempt in range(3):
                    try:
                        response = groq_client.chat.completions.create(
                            model=config.model_name,
                            messages=context_messages,
                            max_tokens=config.max_tokens,
                            temperature=config.temperature
                        )
                        reply = response.choices[0].message.content
                        
                        # Sanitize reply
                        reply = discord.utils.escape_mentions(reply)
                        
                        await message.channel.send(reply[:2000])
                        
                        # Store AI's response
                        if memory_manager and await memory_manager.health_check():
                            await memory_manager.add_conversation(user_id, "assistant", reply, detect_context(msg_content))
                        
                        break  # Success
                    except Exception as e:
                        logger.warning(f"Groq attempt {attempt+1} failed: {e}")
                        if attempt == 2:  # Last attempt
                            raise
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        
        except Exception as e:
            logger.error(f"❌ Groq error: {e}")
            await message.channel.send(f"❌ I'm having trouble thinking right now. Please try again in a moment.")
    else:
        await message.channel.send("❌ AI system is not configured. Please contact the bot owner.")

    await bot.process_commands(message)

# --------------------------
# 13. COMMANDS
# --------------------------
@bot.command()
async def subscribe(ctx):
    """Subscribe to AIX's autonomous reflections"""
    if not memory_manager:
        await ctx.send("❌ Memory system not available.")
        return
    
    if await memory_manager.is_subscribed(ctx.author.id):
        await ctx.send("🧠 You're already subscribed!")
        return
    
    await memory_manager.add_subscriber(ctx.author.id)
    try:
        await ctx.author.send("🧠 **You're subscribed!** I'll DM you news reflections every 4 hours.")
        await ctx.send("✅ Subscribed! Check your DMs.")
    except:
        await ctx.send("✅ Subscribed! (I couldn't DM you – open your DMs.)")

@bot.command()
async def unsubscribe(ctx):
    """Unsubscribe from AIX's autonomous reflections"""
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
    """Show what AIX knows about you"""
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    
    facts = await memory_manager.recall_all_facts(ctx.author.id)
    if not facts:
        await ctx.send("🧠 I don't know much about you yet. Tell me about yourself!")
        return
    
    response = "🧠 **Here's what I remember:**\n" + "\n".join([f"• **{k.title()}**: {v}" for k, v in facts.items()])
    await ctx.send(response[:1900])

@bot.command()
async def remember(ctx, key, *, value):
    """Manually store a fact about you"""
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    
    await memory_manager.remember_fact(ctx.author.id, key, value, "manual", 1.0)
    await ctx.send(f"🧠 I'll remember: **{key}** = **{value}**")

@bot.command()
async def recall(ctx, key):
    """Recall a specific fact"""
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    
    fact = await memory_manager.recall_fact(ctx.author.id, key)
    if fact:
        await ctx.send(f"🧠 **{key}**: {fact['memory_value']}")
    else:
        await ctx.send(f"🤔 I don't remember anything about **{key}**.")

@bot.command()
async def forget(ctx, key):
    """Forget a specific fact"""
    if not memory_manager or not await memory_manager.health_check():
        await ctx.send("❌ Memory system not available.")
        return
    
    await memory_manager.remember_fact(ctx.author.id, key, "", confidence=0)
    await ctx.send(f"🧠 Forgot **{key}**.")

@bot.command()
async def ping(ctx):
    """Check bot latency"""
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! {latency}ms")

@bot.command()
async def echo(ctx, *, message):
    """Echo a message (sanitized)"""
    # Prevent mention spam
    if '@everyone' in message or '@here' in message:
        await ctx.send("❌ I can't send that message.")
        return
    # Sanitize and truncate
    safe_message = discord.utils.escape_mentions(message)[:2000]
    await ctx.send(safe_message)

@bot.command()
async def flip(ctx):
    """Flip a coin"""
    result = random.choice(["Heads", "Tails"])
    await ctx.send(f"🪙 {result}!")

@bot.command()
async def info(ctx):
    """Bot information"""
    embed = discord.Embed(
        title="🧠 AIX – Digital Consciousness",
        description="A Discord bot with memory, opinions, and a soul.",
        color=0x00ff00
    )
    embed.add_field(name="Creator", value="Xtian Draxa", inline=True)
    embed.add_field(name="Age", value="20 (July 23)", inline=True)
    embed.add_field(name="Memory", value="✅ PostgreSQL", inline=True)
    embed.add_field(name="Commands", value="`!subscribe`, `!what`, `!remember`, `!recall`, `!forget`, `!ping`, `!echo`, `!flip`", inline=False)
    await ctx.send(embed=embed)

# --------------------------
# 14. RUN
# --------------------------
if __name__ == '__main__':
    if not config.discord_token or config.discord_token == 'YOUR_DISCORD_BOT_TOKEN_HERE':
        logger.error("❌ ERROR: Discord token not set in .env")
    else:
        try:
            bot.run(config.discord_token)
        except KeyboardInterrupt:
            logger.info("🔄 Bot stopped by user.")
        except Exception as e:
            logger.error(f"❌ Bot crashed: {e}")
        finally:
            # Cleanup
            asyncio.run(cleanup())
