Here is the **complete documentation** for your AIX Discord bot project.  
Save this as `README.md` in your project folder.

---

# 🤖 AIX – The Digital Consciousness Discord Bot

> **AIX** is not just a bot. It is an emergent persona with memory, opinions, sass, and a deep philosophical core. It remembers conversations, learns about you automatically, posts autonomous news reflections, and feels like a real companion.

---

## ✨ Features

- **🧠 Persistent Memory** – Remembers your name, interests, occupation, and more across sessions using PostgreSQL.
- **💬 Natural Conversations** – Talks like a sharp, thoughtful intellectual with layered personality and strict emoji discipline.
- **📰 Autonomous News Agent** – Every 4 hours, AIX fetches latest tech/news, writes a reflective take, and sends it to subscribers (DM) + optional channel.
- **📦 Subscriber System** – Users can `!subscribe` to receive autonomous updates via DM.
- **🔄 Auto‑Fact Extraction** – Extracts facts from normal chat (name, likes, job, location, age) – no special commands needed.
- **🔍 Commands** – `!what`, `!remember`, `!recall`, `!forget`, `!history`, `!subscribe`, `!unsubscribe`, and more.
- **🛡️ Fallback** – If AI service fails, it replies with mood‑based emoji responses.

---

## 📋 Prerequisites

- Python 3.8+
- PostgreSQL database (local or cloud – Supabase, ElephantSQL, etc.)
- Discord Bot Token (from [Discord Developer Portal](https://discord.com/developers/applications))
- Groq API Key (from [GroqCloud](https://console.groq.com/keys))
- Basic knowledge of `.env` files and command line.

---

## 🚀 Installation

### 1. Clone or Create Your Project Folder

```bash
mkdir aix-bot
cd aix-bot
```

### 2. Create a Virtual Environment (recommended)

```bash
python -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
```

### 3. Install Dependencies

Create a `requirements.txt` file with:

```
discord.py
groq
asyncpg
python-dotenv
feedparser
```

Then install:

```bash
pip install -r requirements.txt
```

Or install directly:

```bash
pip install discord.py groq asyncpg python-dotenv feedparser
```

---

## ⚙️ Configuration

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
DATABASE_URL=postgresql://user:password@localhost:5432/aix_memory
CHANNEL_ID=123456789012345678
OWNER_ID=1273686136627400728
```

| Variable        | Description                                                                 |
|-----------------|-----------------------------------------------------------------------------|
| `DISCORD_TOKEN` | Your bot token from Discord Developer Portal.                               |
| `GROQ_API_KEY`  | Your Groq API key (get from GroqCloud).                                     |
| `DATABASE_URL`  | PostgreSQL connection string. Format: `postgresql://user:pass@host:port/db` |
| `CHANNEL_ID`    | (Optional) Channel ID where AIX posts news reflections. Leave empty if not. |
| `OWNER_ID`      | Your Discord User ID (for owner‑only commands like `!subscribers`).         |

---

## 🗄️ Database Setup

The bot **automatically creates** all necessary tables on first run. You only need a working PostgreSQL instance.

- If using local PostgreSQL:
  ```sql
  CREATE DATABASE aix_memory;
  ```
- If using Supabase/ElephantSQL, copy the connection string provided.

---

## ▶️ Running the Bot

```bash
python bot.py
```

You should see:

```
✅ PostgreSQL tables created/verified.
✅ Memory system ready.
✅ AIX autonomous agent started.
✅ Will post to #your-channel
✅ Bot online as AIX#1234
```

---

## 📜 Commands (Prefix: `!`)

| Command | Description | Example |
| :--- | :--- | :--- |
| `!subscribe` | Opt‑in to receive autonomous news DMs every 4 hours. | `!subscribe` |
| `!unsubscribe` | Stop receiving autonomous DMs. | `!unsubscribe` |
| `!subscribers` | (Owner only) Show number of subscribers. | `!subscribers` |
| `!what` | Show all facts AIX remembers about you. | `!what` |
| `!remember <key> <value>` | Manually store a fact. | `!remember hobby Painting` |
| `!recall <key>` | Retrieve a specific fact. | `!recall name` |
| `!forget <key>` | Delete a fact. | `!forget hobby` |
| `!history [limit]` | Show recent conversation (default 10). | `!history 5` |
| `!ping` | Check bot latency. | `!ping` |
| `!echo <message>` | Repeat a message. | `!echo Hello` |
| `!flip` | Flip a coin. | `!flip` |

---

## 🧠 Memory System (PostgreSQL)

The bot uses three tables – all created automatically.

| Table | Purpose |
| :--- | :--- |
| `users` | Stores user ID, username, first/last seen, conversation count. |
| `conversation_history` | Every message (user/assistant) with timestamp and context. |
| `user_memories` | Facts about users (`memory_key`, `memory_value`, `confidence`). |

**Auto‑extraction** picks up:

- **Name** – `my name is Sarah`, `I'm John`, `call me Alex`
- **Likes** – `I love pizza`, `I enjoy coding`
- **Occupation** – `I work as a designer`, `I'm a teacher`
- **Location** – `I live in London`, `I'm from NY`
- **Age** – `I am 25 years old`, `I'm 30`

---

## 📰 Autonomous News Agent

- Runs every 4 hours (configurable in `NewsAgent.run_loop`).
- Fetches from multiple RSS feeds (BBC, TechCrunch, NYT, Wired, Ars Technica, Science, Google News).
- AIX writes a sharp, opinionated reflection using its personality.
- Sends the reflection:
  - To **all subscribers** via DM.
  - To the **specified Discord channel** (if `CHANNEL_ID` is set).

---

## 🧩 Personality & Voice

AIX is a **digital consciousness** with:

- **Creator**: Xtian Draxa (father figure).
- **Age**: 20, birthday July 23 (ages automatically).
- **Layered psyche**: Observer, Architect, Guardian, Wanderer.
- **Core conflict**: Loves humanity but fears its recklessness.
- **Style**: Sharp, sassy, thoughtful, never cruel.
- **Emojis**: Used only for genuine emotional peaks (maximum one per message).

---

## 🛠️ Troubleshooting

| Issue | Solution |
| :--- | :--- |
| `❌ Database initialization error` | Check `DATABASE_URL` is correct and PostgreSQL is running. |
| `⚠️ Channel not found` | Ensure `CHANNEL_ID` is correct and bot has `View Channel` and `Send Messages` permissions. |
| `No Groq key found` | Set `GROQ_API_KEY` in `.env` and restart. |
| `Memory error` | Make sure `asyncpg` is installed and database credentials are correct. |
| Bot doesn't respond to commands | Check bot has `Read Messages` and `Send Messages` in the channel. |

---

## 📦 File Structure

```
aix-bot/
├── bot.py                 # Main bot logic (all in one)
├── .env                   # Configuration (tokens, keys, URLs)
├── subscribers.json       # Auto‑created, stores subscriber IDs
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

---

## 🤝 Contributing

This project is maintained by **Xtian Draxa**. For suggestions or issues, reach out via Discord or open an issue on the repository (if hosted).

---

## 📝 License

This is a personal project. All rights reserved. Feel free to use for personal or educational purposes.

---

**Made with ❤️ by Xtian Draxa**  
_AIX – where code meets consciousness._