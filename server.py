from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import json, os, requests, time, re, hashlib, secrets
from functools import wraps

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, supports_credentials=True)

try:
    import yfinance as yf
    HAS_YFINANCE = True
except:
    HAS_YFINANCE = False

# --- Persistence Layer: Postgres if DATABASE_URL exists, else JSON ---
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_DB = False
conn_pool = None

if DATABASE_URL:
    try:
        import psycopg2
        import psycopg2.extras
        USE_DB = True
        def get_conn():
            return psycopg2.connect(DATABASE_URL, sslmode='require')
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                pw_hash TEXT NOT NULL,
                token TEXT NOT NULL,
                holdings JSONB DEFAULT '[]'::jsonb,
                watchlist JSONB DEFAULT '[]'::jsonb
            );
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("Using Postgres database - persistence enabled")
        except Exception as e:
            print(f"DB init error {e}")
            USE_DB = False
    except Exception as e:
        print(f"psycopg2 not available {e}")
        USE_DB = False
else:
    print("No DATABASE_URL - using temporary JSON (will reset!)")

DATA_FILE = "data.json"
PRICE_CACHE = {}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Referer": "https://finance.yahoo.com/",
    "Origin": "https://finance.yahoo.com"
}

def load_all():
    if USE_DB:
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT username, pw_hash, token, holdings, watchlist FROM users")
            rows = cur.fetchall()
            cur.close(); conn.close()
            users = {}
            for r in rows:
                users[r['username']] = {
                    "pw": r['pw_hash'],
                    "token": r['token'],
                    "holdings": r['holdings'] or [],
                    "watchlist": r['watchlist'] or []
                }
            return {"users": users}
        except Exception as e:
            print(f"db load error {e}")
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                if "holdings" in data and "users" not in data:
                    return {"users": {"demo": {"pw": "", "token": "demo", "holdings": data.get("holdings", []), "watchlist": data.get("watchlist", [])}}}
                return data
        except:
            pass
    return {"users": {}}

def save_user(username, pw_hash, token, holdings, watchlist):
    if USE_DB:
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (username, pw_hash, token, holdings, watchlist)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (username) DO UPDATE SET
                    pw_hash=EXCLUDED.pw_hash,
                    token=EXCLUDED.token,
                    holdings=EXCLUDED.holdings,
                    watchlist=EXCLUDED.watchlist
            """, (username, pw_hash, token, json.dumps(holdings), json.dumps(watchlist)))
            conn.commit()
            cur.close(); conn.close()
            return
        except Exception as e:
            print(f"db save error {e}")
    all_data = load_all()
    all_data.setdefault("users", {})[username] = {"pw": pw_hash, "token": token, "holdings": holdings, "watchlist": watchlist}
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(all_data, f)
    except:
        pass

def get_user_by_token(token):
    if not token:
        return None, None
    all_data = load_all()
    for uname, u in all_data.get("users", {}).items():
        if u.get("token") == token:
            return uname, u
    return None, None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Auth-Token") or request.args.get("token") or (request.get_json(silent=True) or {}).get("token")
        all_data = load_all()
        if not all_data.get("users"):
            return f(*args, **kwargs)
        if not token:
            return jsonify({"error": "auth required"}), 401
        uname, user = get_user_by_token(token)
        if not user:
            return jsonify({"error": "invalid token"}), 401
        request.current_user = uname
        request.current_user_data = user
        return f(*args, **kwargs)
    return decorated

# ---- FIXED market data - no more 401 spam ----
def fetch_with_yfinance(symbol, period="3mo"):
    if not HAS_YFINANCE:
        return None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=period, interval="1d", auto_adjust=False)
        if hist.empty or len(hist) < 2:
            return None
        closes = [round(float(x), 2) for x in hist['Close'].tolist() if x == x]
        if not closes:
            return None
        try:
            info = t.fast_info
            price = float(info.last_price) if hasattr(info, 'last_price') else closes[-1]
        except:
            price = closes[-1]
        prev = closes[-2] if len(closes) > 1 else price
        pct = ((price - prev) / prev * 100) if prev else 0
        name = symbol
        try:
            name = t.info.get('shortName', symbol)
        except:
            pass
        return {"symbol": symbol, "name": name, "price": round(price, 2), "changePct": round(pct, 2), "history": closes}
    except Exception:
        # Silent fail - Yahoo crumb errors are expected on Render free tier
        return None

def fetch_with_requests(symbol, rng="3mo"):
    symbol = symbol.upper()
    for host in ["query1", "query2"]:
        try:
            # Use Yahoo chart API - handle 401 gracefully
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
            if r.status_code == 401:
                continue  # Invalid crumb - try next host or fallback
            if r.status_code != 200:
                continue
            j = r.json()
            result = j.get('chart', {}).get('result', [])
            if not result:
                continue
            data = result[0]
            meta = data.get('meta', {})
            quotes = data.get('indicators', {}).get('quote', [{}])[0]
            closes = quotes.get('close', [])
            closes = [round(float(c),2) for c in closes if c is not None]
            if not closes:
                continue
            price = meta.get('regularMarketPrice') or closes[-1]
            prev = meta.get('previousClose') or (closes[-2] if len(closes)>1 else price)
            pct = ((price-prev)/prev*100) if prev else 0
            return {"symbol": symbol, "name": symbol, "price": round(float(price),2), "changePct": round(pct,2), "history": closes}
        except Exception:
            continue
    return None

def get_price_data(symbol):
    symbol = symbol.upper()
    # cache 60 sec
    if symbol in PRICE_CACHE:
        ts, data = PRICE_CACHE[symbol]
        if time.time() - ts < 60:
            return data
    data = None
    if HAS_YFINANCE:
        data = fetch_with_yfinance(symbol)
    if not data:
        data = fetch_with_requests(symbol)
    if data:
        PRICE_CACHE[symbol] = (time.time(), data)
    return data

# ---- rest of routes (auth, portfolio, news, etc) ----
@app.route('/api/register', methods=['POST'])
def register():
    b = request.get_json(force=True) or {}
    username = (b.get('username') or '').strip().lower()
    pw = b.get('password') or ''
    if not username or not pw or len(username)<3:
        return jsonify({"error": "invalid"}), 400
    all_data = load_all()
    if username in all_data.get("users", {}):
        return jsonify({"error": "exists"}), 400
    pw_hash = hashlib.sha256(pw.encode()).hexdigest()
    token = secrets.token_hex(16)
    save_user(username, pw_hash, token, [], [])
    return jsonify({"token": token, "username": username})

@app.route('/api/login', methods=['POST'])
def login():
    b = request.get_json(force=True) or {}
    username = (b.get('username') or '').strip().lower()
    pw = b.get('password') or ''
    pw_hash = hashlib.sha256(pw.encode()).hexdigest()
    all_data = load_all()
    u = all_data.get("users", {}).get(username)
    if not u or u.get("pw") != pw_hash:
        return jsonify({"error": "invalid"}), 401
    # refresh token
    new_token = secrets.token_hex(16)
    save_user(username, u['pw'], new_token, u.get('holdings',[]), u.get('watchlist',[]))
    return jsonify({"token": new_token, "username": username, "holdings": u.get('holdings',[]), "watchlist": u.get('watchlist',[])})

@app.route('/api/portfolio', methods=['GET'])
@require_auth
def get_portfolio():
    uname = getattr(request, 'current_user', None)
    if not uname:
        # demo mode
        all_data = load_all()
        demo = list(all_data.get("users", {}).values())
        if demo:
            u = demo[0]
        else:
            return jsonify({"holdings": [], "watchlist": []})
        return jsonify({"holdings": u.get('holdings',[]), "watchlist": u.get('watchlist',[])})
    _, user = get_user_by_token(request.headers.get("X-Auth-Token") or request.args.get("token"))
    if not user:
        all_data = load_all()
        user = all_data.get("users", {}).get(uname, {})
    return jsonify({"holdings": user.get('holdings',[]), "watchlist": user.get('watchlist',[])})

@app.route('/api/portfolio', methods=['POST'])
@require_auth
def save_portfolio():
    b = request.get_json(force=True) or {}
    holdings = b.get('holdings', [])
    watchlist = b.get('watchlist', [])
    uname = getattr(request, 'current_user', None)
    if not uname:
        all_data = load_all()
        uname = list(all_data.get("users", {}).keys())[0] if all_data.get("users") else "demo"
    all_data = load_all()
    existing = all_data.get("users", {}).get(uname, {"pw":"", "token": b.get('token','demo')})
    save_user(uname, existing.get('pw',''), existing.get('token','demo'), holdings, watchlist)
    return jsonify({"ok": True})

@app.route('/api/quote/<symbol>')
def quote(symbol):
    data = get_price_data(symbol)
    if not data:
        return jsonify({"error": "not found", "symbol": symbol}), 404
    return jsonify(data)

@app.route('/api/quotes')
def quotes():
    syms = (request.args.get('symbols') or '').upper().split(',')
    syms = [s.strip() for s in syms if s.strip()][:15]
    out = {}
    for s in syms:
        d = get_price_data(s)
        if d:
            out[s] = d
    return jsonify(out)

@app.route('/api/news')
def news():
    # Simplified news - no Yahoo crumb needed
    symbols = (request.args.get('symbols') or 'AAPL,MSFT,SPY').split(',')[:5]
    all_news = []
    for sym in symbols:
        sym=sym.strip().upper()
        if not sym:
            continue
        try:
            url = f"https://query1.finance.yahoo.com/v1/finance/search?q={sym}"
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=5)
            if r.status_code==200:
                j=r.json()
                for n in j.get('news', [])[:3]:
                    all_news.append({"symbol": sym, "title": n.get('title'), "link": n.get('link'), "time": n.get('providerPublishTime',0)})
        except:
            continue
    all_news = sorted(all_news, key=lambda x: x.get('time',0), reverse=True)[:12]
    return jsonify({"news": all_news})

@app.route('/api/health')
def health():
    return jsonify({"ok": True, "has_yfinance": HAS_YFINANCE, "use_db": USE_DB, "version": "3.2-fixed"})

@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        b = request.get_json(force=True) or {}
        prompt = str(b.get('prompt','') or '').strip()
        holdings = b.get('holdings') or []
        parsed = []
        total = 0.0
        cost_basis = 0.0
        gain = 0.0
        for h in holdings:
            try:
                qty = float(h.get('quantity', h.get('qty',0)) or 0)
                avg = float(h.get('avgCost', h.get('avg',0)) or 0)
                cur_raw = h.get('currentPrice', h.get('price',0))
                cur = float(cur_raw or 0)
                cur_for_total = cur if cur>0 else avg
                if qty>0 and avg>0:
                    val = qty*cur_for_total
                    g = (cur_for_total-avg)*qty
                    total+=val
                    cost_basis+=qty*avg
                    gain+=g
                    parsed.append({"symbol": str(h.get('symbol','?')).upper(),"qty": qty,"avg": avg,"cur": cur_for_total,"cur_raw": cur,"value": val,"gain": g})
            except:
                continue
        prompt_upper = prompt.upper()
        mentioned = [p for p in parsed if p['symbol'] in prompt_upper]
        STOPWORDS = {"HOW","IS","MY","THE","AND","FOR","HELP","YOU","WHAT","SHOULD","I","DO","AM","ARE","DOING","WHY","WHEN","WHERE","WILL","CAN","TO","A","OF","IN","ON","WITH","ABOUT","THIS","THAT"}
        tokens = re.findall(r'\b[A-Z]{1,5}\b', prompt_upper)
        extra_tokens = [t for t in tokens if t not in STOPWORDS]
        pl = prompt.lower()
        if not parsed:
            resp = "No holdings found - add positions via Add to Portfolio."
        elif mentioned:
            lines = []
            for p in mentioned:
                pct = ((p['cur']-p['avg'])/p['avg']*100) if p['avg'] else 0
                lines.append(f"{p['symbol']}: {p['qty']} @ ${p['avg']:.2f} now ${p['cur']:.2f}, value ${p['value']:.2f}, {p['gain']:+.2f} ({pct:+.1f}%)")
            resp = "\n".join(lines) + f"\n\nTotal ${total:,.2f} ({gain:+.2f})."
        elif any(w in pl for w in ["how","doing","performance","pnl","profit"]):
            pct = (gain/cost_basis*100) if cost_basis else 0
            resp = f"Portfolio: ${total:,.2f} on ${cost_basis:,.2f} cost, {gain:+.2f} ({pct:+.1f}%) across {len(parsed)} positions."
        else:
            summary = ", ".join([f"{p['symbol']} ${p['value']:.0f}" for p in parsed[:5]])
            resp = f"Portfolio ${total:,.2f} ({gain:+.2f}) across {len(parsed)}: {summary}."
        return jsonify({"response": resp})
    except Exception as e:
        return jsonify({"response": f"Chat error: {e}"}), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def front(path):
    if path.startswith('api/'):
        return jsonify({"error": "not found"}), 404
    if path == "" or path == "index.html":
        if os.path.exists('index.html'):
            return send_from_directory('.', 'index.html')
        else:
            return "Missing index.html", 404
    if os.path.exists(path):
        return send_from_directory('.', path)
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    return jsonify({"ok": True}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
