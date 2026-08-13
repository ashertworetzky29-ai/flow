
import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import yfinance as yf
from datetime import datetime
import uuid
import traceback

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

USERS = {}
TOKENS = {}
PORTFOLIOS = {}

def fetch_quote_data(symbol):
    try:
        t = yf.Ticker(symbol)
        h = t.history(period='3mo')
        if h.empty:
            return None
        price = float(h['Close'].iloc[-1])
        prev = float(h['Close'].iloc[-2]) if len(h) > 1 else price
        pct = ((price - prev) / prev * 100) if prev else 0
        info = {}
        try:
            info = t.info
        except:
            info = {}
        return {
            "symbol": symbol.upper(),
            "price": price,
            "changePct": pct,
            "name": info.get('shortName') or symbol.upper(),
            "history": h['Close'].tolist()[-90:],
            "sector": info.get('sector') or 'Unknown',
            "industry": info.get('industry') or 'Unknown',
            "dividendYield": info.get('dividendYield') or 0,
            "quoteType": info.get('quoteType') or 'EQUITY',
        }
    except Exception as e:
        print(f"quote error {symbol}: {e}")
        return None

# --- API FIRST (before catch-all) ---
@app.route('/api/health')
def health():
    return jsonify({"ok": True, "service": "tickr v8", "time": datetime.now().isoformat()})

@app.route('/api/quote/<symbol>')
def quote(symbol):
    d = fetch_quote_data(symbol)
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify(d)

@app.route('/api/quotes')
def quotes():
    syms = [s.strip().upper() for s in request.args.get('symbols','').split(',') if s.strip()][:15]
    out = {}
    for sym in syms:
        d = fetch_quote_data(sym)
        if d:
            out[sym] = d
    return jsonify(out)

@app.route('/api/earnings')
def earnings():
    syms = [s.strip().upper() for s in request.args.get('symbols','').split(',') if s.strip()][:10]
    earnings_list = []
    today = datetime.now()
    for sym in syms:
        try:
            t = yf.Ticker(sym)
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                for idx in ed.index[:4]:
                    try:
                        dt = idx.to_pydatetime()
                        delta = (dt - today).days
                        if -7 <= delta <= 30:
                            earnings_list.append({"symbol": sym, "date": dt.isoformat(), "daysUntil": delta, "time": "Before Open"})
                    except:
                        continue
        except:
            continue
    earnings_list.sort(key=lambda x: x['date'])
    return jsonify({"earnings": earnings_list})

@app.route('/api/news')
def news():
    syms = [s.strip().upper() for s in request.args.get('symbols','SPY').split(',') if s.strip()][:6]
    all_news = []
    for sym in syms:
        try:
            t = yf.Ticker(sym)
            for item in (getattr(t, 'news', []) or [])[:3]:
                all_news.append({"symbol": sym, "title": item.get('title'), "link": item.get('link'), "time": item.get('providerPublishTime')})
        except:
            continue
    return jsonify({"news": all_news[:12]})

@app.route('/api/auth', methods=['POST'])
@app.route('/api/login', methods=['POST'])
@app.route('/api/register', methods=['POST'])
def auth():
    data = request.get_json() or {}
    u = (data.get('username') or '').strip().lower()
    p = data.get('password') or ''
    if not u or not p:
        return jsonify({"error": "username required"}), 400
    if request.path == '/api/register' and u in USERS:
        return jsonify({"error": "user exists"}), 400
    if u not in USERS:
        USERS[u] = p
        PORTFOLIOS[u] = {"holdings": [{"symbol":"AAPL","quantity":10,"avgCost":175},{"symbol":"JNJ","quantity":5,"avgCost":160},{"symbol":"NEE","quantity":8,"avgCost":70}], "watchlist": ["MSFT","SPY","XLU"]}
    elif USERS[u] != p and request.path != '/api/register':
        return jsonify({"error": "invalid"}), 401
    else:
        USERS[u] = p
    token = str(uuid.uuid4())
    TOKENS[token] = u
    return jsonify({"token": token, "username": u})

@app.route('/api/portfolio', methods=['GET'])
def get_port():
    token = request.headers.get('X-Auth-Token')
    user = TOKENS.get(token)
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(PORTFOLIOS.get(user, {"holdings": [], "watchlist": []}))

@app.route('/api/portfolio', methods=['POST'])
def save_port():
    data = request.get_json() or {}
    token = data.get('token') or request.headers.get('X-Auth-Token')
    user = TOKENS.get(token)
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    PORTFOLIOS[user] = {"holdings": data.get('holdings', []), "watchlist": data.get('watchlist', [])}
    return jsonify({"ok": True})

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json() or {}
    prompt = (data.get('prompt') or '').lower()
    holdings = data.get('holdings') or []
    prices = data.get('prices') or {}
    if 'dividend' in prompt:
        divs = [h for h in holdings if (prices.get(h['symbol'].upper(),{}).get('dividendYield') or 0) > 0.01]
        return jsonify({"response": f"Dividend: {len(divs)} stocks: " + ", ".join([f"{h['symbol']} {(prices.get(h['symbol'].upper(),{}).get('dividendYield',0)*100):.1f}%" for h in divs])})
    return jsonify({"response": f"Portfolio {len(holdings)} holdings. Auto sorted by sector: {', '.join(set([prices.get(h['symbol'].upper(),{}).get('sector','Unknown') for h in holdings]))}"})

# --- STATIC LAST ---
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    # Don't intercept API
    if path.startswith('api/'):
        return jsonify({"error": "Not Found", "path": path}), 404
    # Serve file if exists
    if path and os.path.exists(path):
        return send_from_directory('.', path)
    # Serve index.html for all other routes (SPA)
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    if os.path.exists('tickr-v8.html'):
        return send_from_directory('.', 'tickr-v8.html')
    return jsonify({"ok": True, "message": "tickr API running. Add index.html", "routes": ["/api/health", "/api/quote/<sym>", "/api/quotes", "/api/earnings"]})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"tickr v8 starting on {port}")
    app.run(host='0.0.0.0', port=port)
