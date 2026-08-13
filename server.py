
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

@app.route('/api/quote/<symbol>')
def quote(symbol):
    try:
        t = yf.Ticker(symbol)
        h = t.history(period='3mo')
        price = float(h['Close'].iloc[-1]) if not h.empty else 0
        prev = float(h['Close'].iloc[-2]) if len(h)>1 else price
        pct = ((price-prev)/prev*100) if prev else 0
        info = {}
        try:
            info = t.info
        except:
            info = {}
        return jsonify({
            "symbol": symbol.upper(),
            "price": price,
            "changePct": pct,
            "name": info.get('shortName') or symbol.upper(),
            "history": h['Close'].tolist()[-90:] if not h.empty else [],
            "sector": info.get('sector') or 'Unknown',
            "industry": info.get('industry') or 'Unknown',
            "dividendYield": info.get('dividendYield') or 0,
            "quoteType": info.get('quoteType') or 'EQUITY',
            "marketCap": info.get('marketCap') or 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/quotes')
def quotes():
    symbols = request.args.get('symbols','').split(',')
    out = {}
    for sym in symbols:
        sym=sym.strip().upper()
        if not sym: continue
        try:
            t = yf.Ticker(sym)
            h = t.history(period='3mo')
            price = float(h['Close'].iloc[-1]) if not h.empty else 0
            prev = float(h['Close'].iloc[-2]) if len(h)>1 else price
            pct = ((price-prev)/prev*100) if prev else 0
            info = {}
            try:
                info = t.info
            except:
                info = {}
            out[sym] = {
                "symbol": sym,
                "price": price,
                "changePct": pct,
                "history": h['Close'].tolist()[-90:] if not h.empty else [],
                "name": info.get('shortName') or sym,
                "sector": info.get('sector') or 'Unknown',
                "industry": info.get('industry') or 'Unknown',
                "dividendYield": info.get('dividendYield') or 0,
                "quoteType": info.get('quoteType') or 'EQUITY',
            }
        except:
            continue
    return jsonify(out)

@app.route('/api/earnings')
def earnings():
    symbols_param = request.args.get('symbols','').strip()
    if not symbols_param:
        return jsonify({"earnings": []})
    symbols = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
    earnings_list = []
    today = datetime.now()
    for sym in symbols[:10]:
        try:
            t = yf.Ticker(sym)
            try:
                ed = t.earnings_dates
                if ed is not None and not ed.empty:
                    for idx, row in ed.head(4).iterrows():
                        dt = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else datetime.now()
                        delta = (dt - today).days
                        if delta >= -7 and delta <= 30:
                            earnings_list.append({
                                "symbol": sym,
                                "date": dt.isoformat(),
                                "time": "Before Open",
                                "daysUntil": delta,
                                "source": "earnings_dates"
                            })
                    continue
            except:
                pass
        except:
            continue
    earnings_list.sort(key=lambda x: x['date'])
    return jsonify({"earnings": earnings_list})

@app.route('/api/news')
def news():
    symbols = request.args.get('symbols','SPY').split(',')
    all_news = []
    for sym in symbols[:6]:
        sym=sym.strip().upper()
        if not sym: continue
        try:
            t = yf.Ticker(sym)
            n = t.news or []
            for item in n[:3]:
                all_news.append({"symbol": sym, "title": item.get('title'), "link": item.get('link'), "time": item.get('providerPublishTime')})
        except:
            continue
    return jsonify({"news": all_news[:12]})

# Add your existing /api/portfolio, /api/auth, /api/chat here...

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
