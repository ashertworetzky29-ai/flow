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
        # Create table if not exists
        def get_conn():
            return psycopg2.connect(DATABASE_URL, sslmode='require')
        # init
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
        except Exception as e:
            print(f"DB init error {e}")
            USE_DB = False
    except Exception as e:
        print(f"psycopg2 not available {e}")
        USE_DB = False

DATA_FILE = "data.json"
PRICE_CACHE = {}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Referer": "https://finance.yahoo.com/",
    "Origin": "https://finance.yahoo.com"
}

# ---- storage helpers ----
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
    # fallback json
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # migrate old flat structure
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
    # fallback
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
        # allow demo mode if no users yet
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

# ---- market data (kept from original) ----
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
    except Exception as e:
        print(f"yf {symbol} {e}")
        return None

def fetch_with_requests(symbol, rng="3mo"):
    symbol = symbol.upper()
    for host in ["query1", "query2"]:
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
            r = requests.get(url, params={"range": rng, "interval": "1d"}, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            j = r.json()
            res = (j.get('chart', {}).get('result') or [None])[0]
            if not res:
                continue
            closes = res.get('indicators', {}).get('quote', [{}])[0].get('close', [])
            closes = [c for c in closes if c is not None]
            if len(closes) < 2:
                continue
            meta = res.get('meta', {})
            price = meta.get('regularMarketPrice') or closes[-1]
            prev = meta.get('previousClose') or closes[-2]
            name = meta.get('shortName') or meta.get('longName') or symbol
            pct = ((price - prev) / prev * 100) if prev else 0
            return {"symbol": symbol, "name": name, "price": round(float(price), 2), "changePct": round(float(pct), 2), "history": [round(float(x), 2) for x in closes]}
        except:
            continue
    return None

def get_quote(symbol, rng="3mo"):
    symbol = symbol.upper().strip()
    now = time.time()
    key = f"{symbol}:{rng}"
    if key in PRICE_CACHE and now - PRICE_CACHE[key]['ts'] < 300:
        return PRICE_CACHE[key]['data'], None
    data = fetch_with_yfinance(symbol, rng)
    if not data:
        data = fetch_with_requests(symbol, rng)
    if not data and rng != "1y":
        data = fetch_with_yfinance(symbol, "1y") or fetch_with_requests(symbol, "1y")
    if data:
        PRICE_CACHE[key] = {'ts': now, 'data': data}
        return data, None
    if key in PRICE_CACHE:
        return PRICE_CACHE[key]['data'], "stale"
    return None, "Yahoo blocked"

# ---- routes ----
@app.route('/api/quote')
def quote():
    s = request.args.get('symbol', 'AAPL').upper()
    d, _ = get_quote(s, "3mo")
    if not d:
        return jsonify({"symbol": s, "name": s, "price": 0, "changePct": 0, "history": [], "offline": True})
    return jsonify(d)

@app.route('/api/prices')
def prices():
    syms = [x.strip().upper() for x in request.args.get('symbols', 'AAPL').split(',') if x.strip()][:20]
    out = {}
    for sym in syms:
        d, _ = get_quote(sym, "1mo")
        out[sym] = d if d else {"symbol": sym, "name": sym, "price": 0, "changePct": 0, "history": [], "offline": True}
    return jsonify({"prices": out})

@app.route('/api/me')
@require_auth
def me():
    # return current user or demo
    if hasattr(request, 'current_user_data'):
        u = request.current_user_data
        return jsonify({"holdings": u.get("holdings", []), "watchlist": u.get("watchlist", ["AAPL","NVDA","TSLA","SPY","MSFT","GOOGL"])})
    # demo fallback
    data = load_all()
    if data.get("users"):
        first = list(data["users"].values())[0]
        return jsonify({"holdings": first.get("holdings", []), "watchlist": first.get("watchlist", [])})
    return jsonify({"holdings": [], "watchlist": ["AAPL", "NVDA", "TSLA", "SPY", "MSFT", "GOOGL"]})

@app.route('/api/holdings', methods=['POST', 'OPTIONS'])
@require_auth
def h_post():
    if request.method == 'OPTIONS':
        return '', 200
    b = request.get_json() or {}
    holdings = b.get('holdings', [])
    # save to current user
    if hasattr(request, 'current_user_data'):
        uname = request.current_user
        udata = request.current_user_data
        save_user(uname, udata['pw'], udata['token'], holdings, udata.get('watchlist', []))
    else:
        # no auth setup yet - save to demo
        save_user("demo", "", "demo", holdings, ["AAPL","NVDA","TSLA","SPY","MSFT","GOOGL"])
    return jsonify({"ok": True})

@app.route('/api/watchlist', methods=['POST', 'OPTIONS'])
@require_auth
def w_post():
    if request.method == 'OPTIONS':
        return '', 200
    b = request.get_json() or {}
    watchlist = b.get('watchlist', [])
    if hasattr(request, 'current_user_data'):
        uname = request.current_user
        udata = request.current_user_data
        save_user(uname, udata['pw'], udata['token'], udata.get('holdings', []), watchlist)
    else:
        save_user("demo", "", "demo", [], watchlist)
    return jsonify({"ok": True})

@app.route('/api/news')
def news():
    # keep original logic but with auth-aware holdings
    all_data = load_all()
    holdings_syms = []
    watch_syms = []
    if hasattr(request, 'current_user_data'):
        holdings_syms = [h.get('symbol','').upper() for h in request.current_user_data.get('holdings', []) if h.get('symbol')]
        watch_syms = [s.upper() for s in request.current_user_data.get('watchlist', [])[:3]]
    else:
        if all_data.get("users"):
            first = list(all_data["users"].values())[0]
            holdings_syms = [h.get('symbol','').upper() for h in first.get('holdings', []) if h.get('symbol')]
            watch_syms = [s.upper() for s in first.get('watchlist', [])[:3]]
    symbols = list(dict.fromkeys(holdings_syms + watch_syms))[:6]
    if not symbols:
        symbols = ["AAPL", "SPY"]
    all_news = []
    if HAS_YFINANCE:
        for sym in symbols[:4]:
            try:
                t = yf.Ticker(sym)
                items = t.news[:4] if hasattr(t, 'news') and t.news else []
                for art in items:
                    title = art.get('title') or art.get('content', {}).get('title', '')
                    link = art.get('link') or art.get('content', {}).get('clickThroughUrl', {}).get('url', '') or f"https://finance.yahoo.com/quote/{sym}"
                    if not title:
                        continue
                    all_news.append({
                        "symbol": sym,
                        "title": title,
                        "link": link,
                        "publisher": art.get('publisher',''),
                        "time": art.get('providerPublishTime',0)
                    })
            except:
                pass
    if not all_news:
        for sym in symbols[:3]:
            all_news.append({
                "symbol": sym,
                "title": f"{sym} - Latest news and analysis",
                "link": f"https://finance.yahoo.com/quote/{sym}/news",
                "publisher": "Yahoo Finance",
                "time": 0
            })
    all_news = sorted(all_news, key=lambda x: x.get('time', 0), reverse=True)[:12]
    return jsonify({"news": all_news})

@app.route('/api/health')
def health():
    return jsonify({"ok": True, "has_yfinance": HAS_YFINANCE, "use_db": USE_DB, "version": "3.1"})

@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        b = request.get_json(force=True) or {}
        prompt = str(b.get('prompt', '') or '').strip()
        holdings = b.get('holdings') or []

        parsed = []
        total = 0.0
        cost_basis = 0.0
        gain = 0.0

        for h in holdings:
            try:
                qty = float(h.get('quantity', h.get('qty', 0)) or 0)
                avg = float(h.get('avgCost', h.get('avg', 0)) or 0)
                cur_raw = h.get('currentPrice', h.get('price', 0))
                cur = float(cur_raw or 0)
                cur_for_total = cur if cur > 0 else avg
                if qty > 0 and avg > 0:
                    val = qty * cur_for_total
                    g = (cur_for_total - avg) * qty
                    total += val
                    cost_basis += qty * avg
                    gain += g
                    parsed.append({
                        "symbol": str(h.get('symbol', '?')).upper(),
                        "qty": qty,
                        "avg": avg,
                        "cur": cur_for_total,
                        "cur_raw": cur,
                        "value": val,
                        "gain": g
                    })
            except Exception as e:
                print(f"parse holding error {e}")
                continue

        prompt_upper = prompt.upper()
        mentioned = [p for p in parsed if p['symbol'] in prompt_upper]
        # improved ticker extraction with blocklist
        STOPWORDS = {"HOW","IS","MY","THE","AND","FOR","HELP","YOU","WHAT","SHOULD","I","DO","AM","ARE","DOING","WHY","WHEN","WHERE","WILL","CAN","TO","A","OF","IN","ON","WITH","ABOUT","THIS","THAT"}
        tokens = re.findall(r'\b[A-Z]{1,5}\b', prompt_upper)
        extra_tokens = [t for t in tokens if t not in STOPWORDS]

        pl = prompt.lower()

        if not parsed:
            resp = "No holdings found - add positions via Add to Portfolio (Symbol, Quantity, Total Cost or Avg Cost). I use your real cost basis + live yfinance prices."
        elif mentioned:
            lines = []
            for p in mentioned:
                pct = ((p['cur'] - p['avg']) / p['avg'] * 100) if p['avg'] else 0
                offline_note = " (live price offline, using avg)" if p['cur_raw'] == 0 else ""
                lines.append(f"{p['symbol']}: {p['qty']} @ ${p['avg']:.2f} avg, now ${p['cur']:.2f}{offline_note}, value ${p['value']:.2f}, {p['gain']:+.2f} ({pct:+.1f}%)")
            resp = "Here is your " + "/".join([p['symbol'] for p in mentioned]) + " position:\n" + "\n".join(lines) + f"\n\nTotal portfolio ${total:,.2f} ({gain:+.2f})."
        elif any(w in pl for w in ["how", "doing", "performance", "pnl", "profit", "am i"]):
            pct = (gain / cost_basis * 100) if cost_basis else 0
            status = "up" if gain > 0 else "down" if gain < 0 else "flat"
            detail_lines = []
            for p in parsed[:6]:
                detail_lines.append(f"{p['symbol']}: ${p['value']:.2f} ({p['gain']:+.2f})")
            details = "\n".join(detail_lines)
            resp = f"Portfolio: ${total:,.2f} value on ${cost_basis:,.2f} cost, {gain:+.2f} ({pct:+.1f}%) {status} across {len(parsed)} positions.\n{details}\n\nAll from real Yahoo Finance."
        elif "divers" in pl:
            sym_list = ", ".join([p['symbol'] for p in parsed])
            if len(parsed) < 3:
                resp = f"You have {len(parsed)} positions (${total:,.2f}) - concentrated. You own: {sym_list}. Consider adding 2-3 more sectors like SPY, QQQ, or different industries."
            else:
                winners = [p for p in parsed if p['gain'] > 0]
                losers = [p for p in parsed if p['gain'] < 0]
                largest = max(parsed, key=lambda x: x['value'])
                resp = f"{len(parsed)} positions, ${total:,.2f}. {len(winners)} winners, {len(losers)} losers. Largest: {largest['symbol']} (${largest['value']:.2f}). To diversify, consider broad ETFs like SPY/VOO or sectors different from {sym_list}."
        elif extra_tokens:
            sym_to_check = extra_tokens[0]
            owned_syms = [p['symbol'] for p in parsed]
            if sym_to_check not in owned_syms:
                owned_str = ", ".join(owned_syms)
                example = parsed[0]['symbol'] if parsed else "AAPL"
                resp = f"You do not own {sym_to_check} - your holdings are: {owned_str} (${total:,.2f} total). Add {sym_to_check} via Add to Portfolio if you bought it, or ask about a symbol you own like {example}."
            else:
                p = next((x for x in parsed if x['symbol'] == sym_to_check), None)
                if p:
                    resp = f"{p['symbol']}: {p['qty']} @ ${p['avg']:.2f} now ${p['cur']:.2f} = ${p['value']:.2f} ({p['gain']:+.2f})"
                else:
                    resp = f"{sym_to_check} not in portfolio."
        else:
            summary_parts = []
            for p in parsed[:5]:
                summary_parts.append(f"{p['symbol']} ${p['value']:.0f}")
            summary = ", ".join(summary_parts)
            resp = f"Portfolio ${total:,.2f} ({gain:+.2f}) across {len(parsed)} positions: {summary}. Ask how is my AAPL or how am I doing or help diversify."

        return jsonify({"response": resp, "debug": {"count": len(parsed), "total": total}})
    except Exception as e:
        print(f"chat error {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"response": f"Chat error but portfolio tracked: {e}"}), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def front(path):
    if path.startswith('api/'):
        return jsonify({"error": "not found"}), 404
    if path == "" or path == "index.html":
        if os.path.exists('index.html'):
            return send_from_directory('.', 'index.html')
        else:
            return "Missing index.html - upload it", 404
    if os.path.exists(path):
        return send_from_directory('.', path)
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    return jsonify({"ok": True}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
