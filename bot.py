import asyncio, os, json, logging, sys, aiosqlite, aiohttp, time, re, html, cachetools, random, collections, shutil
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from twikit import Client

# 🛡️ LOGGING
log_buffer = collections.deque(maxlen=50)
class ListHandler(logging.Handler):
    def emit(self, record): log_buffer.append(self.format(record))

handlers = [ListHandler(), logging.StreamHandler(sys.stdout)]
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', handlers=handlers)
logger = logging.getLogger("ApexFinal")

# 🛡️ CONFIG
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    SCAN_JITTER = (3, 6)
    PRELOAD_WATCHLIST = ["earnbybase", "samksr0", "elonmusk"]
    RAW_COOKIES = [
        [{"name": "auth_token", "value": "014d074b2ac08cc316790838c2e45536e85e0d69"}, {"name": "ct0", "value": "245cd113842dd630eea03600e545a0e7715ff55900a19a8ca1a974ed6163fdf572536905cab1cb6dbcc84c0ba26eae7f723aa5daf203dc0784fc72b05cbbf93c94b7cb19d8cdce5d0529825b41980f20"}],
        [{"name": "auth_token", "value": "fc3c8355cfc2880cb2aafe8808090627a71f1912"}, {"name": "ct0", "value": "2f210ef5231797e2b147402652e494dabfd064d365255f761d64d694c6869acdad3415aeabaac48eb08b39124206f45101cc5131c7af38cf7ad023acee63a1f28015c3544510da26b6c93bca7d2483e8"}]
    ]

dedupe_cache = cachetools.TTLCache(maxsize=1000, ttl=600)

# --- UTILS ---
class DexAlpha:
    @staticmethod
    async def get_stats(ca):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}") as r:
                    d = await r.json(); p = d.get('pairs', [{}])[0]
                    return f"\n\n💰 Price: ${p.get('priceUsd','N/A')}\n📊 MCAP: ${int(float(p.get('fdv',0))):,}"
            except: return ""

class MediaHunter:
    @staticmethod
    def get_best_media(tweet):
        m_list = getattr(tweet, 'media', [])
        if not m_list: return None
        m = m_list[0]
        if 'video_info' in m:
            vars = m['video_info'].get('variants', [])
            best_vid = max([v for v in vars if v.get('content_type') == 'video/mp4'], key=lambda x: x.get('bitrate', 0), default=None)
            if best_vid: return {'type': 'video', 'url': best_vid['url']}
        return {'type': 'photo', 'url': m.get('media_url_https')}

class DatabaseManager:
    def __init__(self): self.db_file = "bot_memory.db"
    async def initialize(self):
        self.pool = await aiosqlite.connect(self.db_file, timeout=60.0)
        await self.pool.execute("PRAGMA journal_mode=WAL")
        tables = [
            'CREATE TABLE IF NOT EXISTS watchlist (username TEXT PRIMARY KEY, config TEXT)',
            'CREATE TABLE IF NOT EXISTS last_seen (username TEXT PRIMARY KEY, tweet_id TEXT)',
            'CREATE TABLE IF NOT EXISTS last_likes (username TEXT PRIMARY KEY, like_id TEXT)',
            'CREATE TABLE IF NOT EXISTS user_stats (username TEXT PRIMARY KEY, following_count INTEGER, followers_count INTEGER)',
            'CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, cookies TEXT, is_active BOOLEAN DEFAULT 1)'
        ]
        for t in tables: await self.pool.execute(t)
        async with self.pool.execute("SELECT COUNT(*) FROM accounts") as cur:
            if (await cur.fetchone())[0] == 0:
                for c in Config.RAW_COOKIES: await self.pool.execute("INSERT INTO accounts (cookies) VALUES (?)", (json.dumps(c),))
        default_conf = json.dumps({"tweets":1,"reposts":1,"likes":1,"following":1,"followers":1})
        for user in Config.PRELOAD_WATCHLIST:
            await self.pool.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (user, default_conf))
        await self.pool.commit()

    async def execute(self, query, params=()):
        await self.pool.execute(query, params)
        await self.pool.commit()
    async def fetch_one(self, query, params=()):
        async with self.pool.execute(query, params) as cur: return await cur.fetchone()
    async def fetch_all(self, query, params=()):
        async with self.pool.execute(query, params) as cur: return await cur.fetchall()

db = DatabaseManager()

class TwitterManager:
    def __init__(self): self.clients = []
    async def refresh(self):
        async with db.pool.execute("SELECT id, cookies FROM accounts WHERE is_active=1") as cur:
            accs = await cur.fetchall()
            self.clients = []
            for aid, cks in accs:
                try:
                    cl = Client('en-US', timeout=15)
                    cl.set_cookies({k['name']: k['value'] for k in json.loads(cks)})
                    self.clients.append({'cl': cl, 'id': aid, 'cd': 0})
                except: pass
    def get_client(self):
        ready = [c for c in self.clients if time.time() > c['cd']]
        return random.choice(ready) if ready else None
    def lock_client(self, client):
        logger.warning(f"⚠️ Rate Limit: Agent {client['id']}")
        client['cd'] = time.time() + 900 
tm = TwitterManager()

# --- COMMANDS ---
async def cmd_start(u, c):
    await u.effective_message.reply_text("🦅 *V90 Apex-Final*\nSystem: Fully Operational.\n/help for commands.", parse_mode=ParseMode.MARKDOWN)

async def cmd_add(u, c):
    user = (c.args[0].replace("@","").lower() if c.args else 
            re.search(r"x\.com/([a-zA-Z0-9_]+)", u.effective_message.text).group(1).lower() if "x.com" in u.effective_message.text else None)
    if not user: return await u.effective_message.reply_text("Usage: /add username")
    conf = {"tweets":1,"reposts":1,"likes":1,"following":1,"followers":1}
    await db.execute("INSERT OR REPLACE INTO watchlist VALUES (?, ?)", (user, json.dumps(conf)))
    client = tm.get_client()
    if client:
        try:
            u_obj = await client['cl'].get_user_by_screen_name(user)
            t = await client['cl'].get_user_tweets(u_obj.id, 'Tweets', count=1)
            if t: await db.execute("INSERT OR REPLACE INTO last_seen VALUES (?, ?)", (user, str(t[0].id)))
            await db.execute("INSERT OR REPLACE INTO user_stats VALUES (?, ?, ?)", (user, u_obj.friends_count, u_obj.followers_count))
        except: pass
    await u.effective_message.reply_text(f"📡 Tracking @{user}", reply_markup=get_config_kb(user, conf), parse_mode=ParseMode.MARKDOWN)

async def cmd_logs(u, c):
    logs = "".join(list(log_buffer))
    await u.effective_message.reply_text(f"<pre>{html.escape(logs)}</pre>", parse_mode=ParseMode.HTML)

async def cmd_list(u, c):
    users = await db.fetch_all("SELECT username FROM watchlist")
    msg = "📋 *Watchlist:*\n" + "\n".join([f"• @{r[0]}" for r in users]) if users else "Empty."
    await u.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_health(u, c):
    await u.effective_message.reply_text(f"🏥 *Status*\nAgents: {len(tm.clients)}\nMode: Limitless (No Sleep)", parse_mode=ParseMode.MARKDOWN)

async def cmd_cookies(u, c):
    try:
        raw = u.effective_message.text.split(None, 1)[1]
        await db.execute("INSERT INTO accounts (cookies) VALUES (?)", (json.dumps(json.loads(raw)),))
        await tm.refresh()
        await u.effective_message.reply_text("✅ Agent Injected")
    except: await u.effective_message.reply_text("Usage: /cookies [json]")

async def cmd_backup(u, c):
    await u.effective_message.reply_document(document=open("bot_memory.db", "rb"), caption="📦 Backup")

async def cmd_clean(u, c):
    await db.execute("VACUUM")
    log_buffer.clear()
    await u.effective_message.reply_text("🧹 Cleaned")

def get_config_kb(user, conf):
    def s(k): return "✅" if conf.get(k, 0) else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{s('tweets')} Tweets", callback_data=f"cfg_{user}_tweets"), InlineKeyboardButton(f"{s('reposts')} Reposts", callback_data=f"cfg_{user}_reposts")],
        [InlineKeyboardButton(f"{s('likes')} Likes", callback_data=f"cfg_{user}_likes"), InlineKeyboardButton(f"{s('following')} Following", callback_data=f"cfg_{user}_following")],
        [InlineKeyboardButton(f"{s('followers')} Followers", callback_data=f"cfg_{user}_followers")],
        [InlineKeyboardButton("💾 Save", callback_data="cfg_done")]
    ])

async def cmd_config(u, c):
    user = c.args[0].replace("@","").lower() if c.args else None
    if not user: return await u.effective_message.reply_text("Usage: /config username")
    res = await db.fetch_one("SELECT config FROM watchlist WHERE username=?", (user,))
    if res: await u.effective_message.reply_text(f"⚙️ Settings: @{user}", reply_markup=get_config_kb(user, json.loads(res[0])), parse_mode=ParseMode.MARKDOWN)

async def btn_handler(u, c):
    q = u.callback_query; await q.answer()
    if q.data == "cfg_done": return await q.message.edit_text("✅ Saved")
    if q.data.startswith("cfg_"):
        _, user, key = q.data.split("_")
        res = await db.fetch_one("SELECT config FROM watchlist WHERE username=?", (user,))
        if res:
            conf = json.loads(res[0]); conf[key] = 0 if conf.get(key, 0) else 1
            await db.execute("UPDATE watchlist SET config=? WHERE username=?", (json.dumps(conf), user))
            await q.message.edit_reply_markup(reply_markup=get_config_kb(user, conf))

async def scan_job(context):
    try: await omni_scan(context)
    finally: context.job_queue.run_once(scan_job, random.uniform(*Config.SCAN_JITTER))

async def backup_job(context):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        shutil.copy("bot_memory.db", f"bot_memory_backup.db")
    except: pass

async def omni_scan(context):
    client = tm.get_client()
    if not client: return
    users = await db.fetch_all("SELECT username, config FROM watchlist")
    if not users: return
    for user, conf_raw in users:
        conf = json.loads(conf_raw)
        try:
            u_obj = await client['cl'].get_user_by_screen_name(user)
            if conf.get('tweets') or conf.get('reposts'):
                tweets = await client['cl'].get_user_tweets(u_obj.id, 'Tweets', count=1)
                if tweets:
                    top = tweets[0]
                    is_repost = hasattr(top, 'retweeted_status')
                    if (is_repost and conf.get('reposts')) or (not is_repost and conf.get('tweets')):
                        last = await db.fetch_one("SELECT tweet_id FROM last_seen WHERE username=?", (user,))
                        if not last or str(top.id) != last[0]:
                            await db.execute("INSERT OR REPLACE INTO last_seen VALUES (?, ?)", (user, str(top.id)))
                            ca_match = re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}|0x[a-fA-F0-9]{40}', getattr(top, 'text', ''))
                            stats = await DexAlpha.get_stats(ca_match.group(0)) if ca_match else ""
                            msg = f"{'🔁' if is_repost else '🔔'} @{user}\n\n{html.escape(getattr(top, 'text', ''))}{stats}\n\n🔗 https://x.com/{user}/status/{top.id}"
                            media = MediaHunter.get_best_media(top)
                            if media:
                                if media['type'] == 'video': await context.bot.send_video(Config.CHAT_ID, video=media['url'], caption=msg, parse_mode=ParseMode.MARKDOWN)
                                else: await context.bot.send_photo(Config.CHAT_ID, photo=media['url'], caption=msg, parse_mode=ParseMode.MARKDOWN)
                            else:
                                await context.bot.send_message(Config.CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)
            if conf.get('likes'):
                likes = await client['cl'].get_user_likes(u_obj.id, count=1)
                if likes:
                    top_like = likes[0]
                    last = await db.fetch_one("SELECT like_id FROM last_likes WHERE username=?", (user,))
                    if not last or str(top_like.id) != last[0]:
                        await db.execute("INSERT OR REPLACE INTO last_likes VALUES (?, ?)", (user, str(top_like.id)))
                        msg = f"❤️ *LIKED* @{user}\n\n{html.escape(getattr(top_like, 'text', ''))}\n\n🔗 https://x.com/{user}/status/{top_like.id}"
                        await context.bot.send_message(Config.CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN)
            if conf.get('following'):
                stat = await db.fetch_one("SELECT following_count, followers_count FROM user_stats WHERE username=?", (user,))
                if stat and u_obj.friends_count > stat[0]:
                     await context.bot.send_message(Config.CHAT_ID, f"👣 *FOLLOWING CHANGED* @{user}\nNew: {u_obj.friends_count} (Was {stat[0]})", parse_mode=ParseMode.MARKDOWN)
                if stat and (u_obj.friends_count != stat[0] or u_obj.followers_count != stat[1]):
                    await db.execute("UPDATE user_stats SET following_count=?, followers_count=? WHERE username=?", (u_obj.friends_count, u_obj.followers_count, user))
        except Exception as e:
            if "429" in str(e): tm.lock_client(client)
        await asyncio.sleep(2)

async def post_init(application):
    await db.initialize(); await tm.refresh()
    cmds = [BotCommand("add", "Add"), BotCommand("logs", "Logs"), BotCommand("list", "List"), BotCommand("cookies", "Inject"), BotCommand("health", "Status"), BotCommand("clean", "Clean"), BotCommand("backup", "Backup")]
    await application.bot.set_my_commands(cmds)
    application.job_queue.run_once(scan_job, 1)
    application.job_queue.run_repeating(backup_job, interval=21600, first=3600)
    await application.bot.send_message(Config.CHAT_ID, "🚀 *V90 Apex-Final Online*")

if __name__ == "__main__":
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handlers([CommandHandler("start", cmd_start), CommandHandler("add", cmd_add), CommandHandler("config", cmd_config), CommandHandler("cookies", cmd_cookies), CommandHandler("health", cmd_health), CommandHandler("logs", cmd_logs), CommandHandler("list", cmd_list), CommandHandler("clean", cmd_clean), CommandHandler("backup", cmd_backup), CallbackQueryHandler(btn_handler)])
    app.add_handler(MessageHandler(filters.Regex(r"(twitter\.com|x\.com)/"), cmd_add))
    app.run_polling()
