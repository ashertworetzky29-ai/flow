
import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import yfinance as yf
from datetime import datetime
import uuid
import traceback
import random
import re

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

USERS = {}
TOKENS = {}
PORTFOLIOS = {}

def fetch_quote_data(symbol):
    try:
        t = yf.Ticker(symbol)
        h = None
        try:
            h = t.history(period='3mo')
        except:
            h = None
        if h is None or getattr(h, 'empty', True):
            try:
                h = t.history(period='1mo')
            except:
                pass
        if h is None or getattr(h, 'empty', True):
            return None
        try:
            price = float(h['Close'].iloc[-1])
            prev = float(h['Close'].iloc[-2]) if len(h) > 1 else price
            pct = ((price - prev) / prev * 100) if prev else 0
        except:
            return None
        info = {}
        try:
            info = t.info or {}
        except:
            info = {}
        try:
            history_list = [float(x) for x in h['Close'].tolist()[-90:]]
        except:
            history_list = [price]
        return {
            "symbol": symbol.upper(),
            "price": price,
            "changePct": pct,
            "name": (info.get('shortName') or info.get('longName') or symbol.upper()),
            "history": history_list,
            "sector": info.get('sector') or 'Unknown',
            "industry": info.get('industry') or 'Unknown',
            "dividendYield": info.get('dividendYield') or info.get('trailingAnnualDividendYield') or 0,
            "quoteType": info.get('quoteType') or 'EQUITY',
            "marketCap": info.get('marketCap') or 0,
            "pe": info.get('trailingPE') or info.get('forwardPE') or 0,
        }
    except Exception as e:
        print(f"quote error {symbol}: {e}")
        return None

def parse_news_item(item):
    try:
        if not isinstance(item, dict):
            return None
        title = None
        link = None
        pub_time = None
        publisher = None
        if 'content' in item and isinstance(item['content'], dict):
            content = item['content']
            title = content.get('title')
            ct = content.get('clickThroughUrl')
            if ct and isinstance(ct, dict):
                link = ct.get('url')
            if not link:
                can = content.get('canonicalUrl')
                if can and isinstance(can, dict):
                    link = can.get('url')
            if not link:
                link = content.get('link') or content.get('url')
            pub_time = content.get('pubDate') or content.get('displayTime')
            provider = content.get('provider')
            if provider and isinstance(provider, dict):
                publisher = provider.get('displayName')
        else:
            title = item.get('title')
            link = item.get('link') or item.get('url')
            pub_time = item.get('providerPublishTime') or item.get('pubDate')
            publisher = item.get('publisher')
        if not title or not link:
            return None
        if not isinstance(link, str) or not link.startswith('http'):
            return None
        if link.rstrip('/').endswith('yahoo.com') or link.rstrip('/') == 'https://finance.yahoo.com/news':
            return None
        if len(link) < 25:
            return None
        return {"title": title, "link": link, "time": pub_time, "publisher": publisher or "Yahoo Finance"}
    except:
        return None

@app.route('/api/health')
def health():
    return jsonify({"ok": True, "service": "tickr v12 conversational", "time": datetime.now().isoformat()})

@app.route('/api/quote/<symbol>')
def quote(symbol):
    try:
        d = fetch_quote_data(symbol)
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)
    except Exception as e:
        print(f"quote {symbol} error {e}")
        return jsonify({"error": "internal"}), 500

@app.route('/api/quotes')
def quotes():
    try:
        syms = [s.strip().upper() for s in request.args.get('symbols','').split(',') if s.strip()][:20]
        out = {}
        for sym in syms:
            try:
                d = fetch_quote_data(sym)
                if d:
                    out[sym] = d
            except:
                continue
        return jsonify(out)
    except:
        return jsonify({}), 200

@app.route('/api/earnings')
def earnings():
    try:
        syms = [s.strip().upper() for s in request.args.get('symbols','').split(',') if s.strip()][:10]
        earnings_list = []
        today = datetime.now()
        for sym in syms:
            try:
                t = yf.Ticker(sym)
                ed = None
                try:
                    ed = t.earnings_dates
                except:
                    ed = None
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
    except:
        return jsonify({"earnings": []}), 200

@app.route('/api/news')
def news():
    try:
        syms = [s.strip().upper() for s in request.args.get('symbols','SPY').split(',') if s.strip()][:8]
        all_news = []
        seen = set()
        for sym in syms:
            try:
                t = yf.Ticker(sym)
                raw_news = getattr(t, 'news', []) or []
                for raw in raw_news[:6]:
                    parsed = parse_news_item(raw)
                    if not parsed:
                        continue
                    if parsed['link'] in seen:
                        continue
                    seen.add(parsed['link'])
                    all_news.append({"symbol": sym, "title": parsed['title'], "link": parsed['link'], "time": parsed['time'], "publisher": parsed['publisher']})
                    if len(all_news) >= 20:
                        break
            except:
                continue
        if not all_news:
            for sym in syms[:4]:
                all_news.append({"symbol": sym, "title": f"{sym} latest news", "link": f"https://finance.yahoo.com/quote/{sym}/news/", "time": datetime.now().isoformat(), "publisher": "Yahoo Finance"})
        return jsonify({"news": all_news[:20]})
    except Exception as e:
        print(f"news error {e}")
        return jsonify({"news": []}), 200

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
        PORTFOLIOS[u] = {"holdings": [{"symbol":"AAPL","quantity":10,"avgCost":175},{"symbol":"JNJ","quantity":5,"avgCost":160},{"symbol":"NEE","quantity":8,"avgCost":70}], "watchlist": ["MSFT","SPY","XLU","VYM","SCHD"]}
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
    try:
        data = request.get_json() or {}
        token = data.get('token') or request.headers.get('X-Auth-Token')
        user = TOKENS.get(token)
        if not user:
            return jsonify({"error": "unauthorized"}), 401
        holdings = data.get('holdings', [])
        watchlist = data.get('watchlist', [])
        PORTFOLIOS[user] = {"holdings": holdings, "watchlist": watchlist}
        return jsonify({"ok": True})
    except Exception as e:
        print(f"save error {e}")
        return jsonify({"ok": False}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json() or {}
        prompt = (data.get('prompt') or '').strip()
        lower = prompt.lower()
        holdings = data.get('holdings') or []
        prices = data.get('prices') or {}
        current = data.get('currentSymbol') or 'SPY'

        total_val = 0
        total_cost = 0
        sectors = {}
        dividend_stocks = []
        gainers = []
        losers = []

        for h in holdings:
            try:
                sym = (h.get('symbol') or '').upper()
                q = prices.get(sym) or {}
                qty = float(h.get('quantity') or 0)
                avg = float(h.get('avgCost') or 0)
                price = float(q.get('price') or avg or 0)
                val = qty * price
                cost = qty * avg
                total_val += val
                total_cost += cost
                sector = q.get('sector') or 'Unknown'
                sectors[sector] = sectors.get(sector, 0) + val
                dy = q.get('dividendYield') or 0
                if dy > 0.01:
                    dividend_stocks.append((h.get('symbol'), dy, sector, price, qty, val))
                pct = ((price - avg)/avg*100) if avg else 0
                gain = (price - avg) * qty
                if pct > 0.5:
                    gainers.append((h.get('symbol'), pct, gain, price, sector))
                elif pct < -0.5:
                    losers.append((h.get('symbol'), pct, gain, price, sector))
            except:
                continue

        total_gain = total_val - total_cost
        total_gain_pct = (total_gain/total_cost*100) if total_cost else 0
        top_sector = max(sectors, key=sectors.get) if sectors else None
        top_pct = sectors.get(top_sector, 0)/total_val*100 if total_val and top_sector else 0

        # Helper for conversational tone
        def portfolio_context():
            if total_val == 0:
                return "Your portfolio is still loading prices — give it a second."
            return f"You have {len(holdings)} holdings totaling about ${total_val:.0f}."

        # 1. Greetings and small talk
        if re.match(r'^(hey|hi|hello|yo|sup|whats up|what\'s up|howdy|good morning|good afternoon|good evening)\b', lower):
            if len(lower.split()) <= 4:
                return jsonify({"response": f"Hey there. {portfolio_context()} Currently you're {'up' if total_gain_pct>0 else 'down' if total_gain_pct<0 else 'about flat'} {total_gain_pct:+.1f}% overall. What would you like to talk about? I can walk through your allocation, dividends, or just chat about what's happening in the market."})

        if any(p in lower for p in ['how are you', 'how r you', 'how are u']):
            return jsonify({"response": f"I'm doing well, thanks for asking. I've been keeping an eye on your {len(holdings)} holdings — {portfolio_context().lower()} The largest piece is {top_sector or 'a mix of sectors'} at {top_pct:.0f}% if that helps frame things. How are you feeling about the market lately?"})

        if any(p in lower for p in ['who are you', 'what are you', 'what can you do']):
            return jsonify({"response": f"I'm Tickr AI — your portfolio assistant built into Tickr. I know your current holdings, watchlist, and live prices, so I can talk about your actual allocation, not just general advice.\n\nI can:\n• Break down your sector allocation, dividend income, gainers and losers\n• Talk through any ticker you mention with real price and context\n• Discuss market conditions, diversification, and what I'm seeing in your mix\n• Help you edit — say 'delete AAPL' or 'edit AAPL to 15 shares'\n\nI'm not a financial advisor, but I can help you think through things. What do you want to dig into?"})

        # 2. Market conversation - open ended
        if any(p in lower for p in ["how's market", "how is market", "market doing", "market today", "what's happening in the market", "market update", "market sentiment", "market conditions"]):
            spy_q = prices.get('SPY') or {}
            spy_chg = spy_q.get('changePct', 0)
            if spy_chg > 1:
                market_desc = "having a strong day"
            elif spy_chg > 0.2:
                market_desc = "up a bit today"
            elif spy_chg > -0.2:
                market_desc = "pretty flat today, chopping around"
            elif spy_chg > -1:
                market_desc = "down a bit today"
            else:
                market_desc = "under some pressure today"
            return jsonify({"response": f"The broader market is {market_desc} — SPY is {spy_chg:+.2f}% on the day. In your portfolio, you're {total_gain_pct:+.1f}% overall, with {top_sector or 'your top sector'} making up {top_pct:.0f}% of your holdings.\n\nWhen I look at your mix, you have {len(gainers)} positions up and {len(losers)} down. {'That balance is actually pretty normal for a diversified portfolio.' if len(gainers)>0 and len(losers)>0 else ''} Is there a particular part of the market you're watching — tech, energy, healthcare? I can talk through how your allocation lines up with that."})

        if any(p in lower for p in ['bull market', 'bear market', 'bull or bear', 'are we in a bull', 'recession', 'correction']):
            return jsonify({"response": f"That's the question everyone asks. Honestly, the market rarely fits neatly into bull or bear labels day-to-day — it tends to move in cycles.\n\nLooking at your portfolio as a reference: you're {total_gain_pct:+.1f}% overall, with {len(gainers)} winners and {len(losers)} losers. Your largest concentration is {top_sector or 'one sector'} at {top_pct:.0f}%. In a true bull market, you'd usually see more breadth, and in a bear, more uniform pressure.\n\nWhat matters more than the label is how your allocation handles different conditions. You have {len(dividend_stocks)} dividend payers, which tend to be more defensive, and the rest is more growth-oriented. Are you thinking about making your portfolio more defensive or are you comfortable with the current mix?"})

        if any(p in lower for p in ['should i buy', 'is it a good time to buy', 'what should i buy', 'buy now', 'is it time to sell']):
            return jsonify({"response": f"I can't give you a direct buy or sell recommendation — that's financial advice and depends on your goals, timeline, and risk tolerance.\n\nWhat I can do is help you think through it with your actual numbers: right now you're at {top_pct:.0f}% {top_sector or 'in one area'}, with ${total_val:.0f} total value. You have {len(dividend_stocks)} dividend positions averaging {(sum(y for _,y,_,_,_,_ in dividend_stocks)/len(dividend_stocks)*100) if dividend_stocks else 0:.1f}% yield, which gives you some income stability.\n\nIf you're considering a new purchase, a few useful questions are:\n• Would it increase or reduce your concentration in {top_sector or 'your top sector'}?\n• Does it add dividend income or growth exposure you don't have?\n• How does it fit with your current {total_gain_pct:+.1f}% overall gain/loss — are you looking to add risk or reduce it?\n\nTell me a ticker or sector you're considering and I can walk through how it would fit."})

        # 3. Specific ticker conversation - detect any ticker mention
        single_sym = None
        if len(prompt.strip().split()) == 1 and prompt.strip().isalpha() and 1 <= len(prompt.strip()) <= 5:
            single_sym = prompt.strip().upper()
        else:
            # Look for "what about X", "thoughts on X", "is X", "X stock", etc
            m = re.search(r'(?:about|on|is|for|think.*|thoughts.*|how.*|what.*)\s+([A-Z]{1,5})\b', prompt.upper())
            if m:
                cand = m.group(1)
                if cand not in ['WHAT','ABOUT','THIS','THAT','YOUR','SHOULD','THERE','THEIR','THINK','THOUGHTS','STOCK','MARKET','MONEY','PORTFOLIO']:
                    single_sym = cand
            # Also check if prompt is like "AAPL?" or "NVDA thoughts"
            if not single_sym:
                m2 = re.search(r'\b([A-Z]{2,5})\b', prompt.upper())
                if m2 and m2.group(1) in prices:
                    # If it's a known symbol in cache, treat as ticker question
                    if len(lower.split()) <= 6:
                        single_sym = m2.group(1)

        if single_sym:
            q = prices.get(single_sym) or {}
            # Try to fetch if not in cache but looks like ticker
            if not q and len(single_sym) <= 5:
                # Don't fetch here, just give generic
                return jsonify({"response": f"{single_sym} — I don't have live data for that one loaded yet. If you add it to your watchlist I can pull price, sector, and dividend info. What made you interested in {single_sym}? Is it for growth, income, or diversification?"})
            if q:
                price = q.get('price', 0)
                sector = q.get('sector', 'Unknown')
                dy = q.get('dividendYield', 0)
                chg = q.get('changePct', 0)
                name = q.get('name', single_sym)
                pe = q.get('pe', 0)
                holding = next((h for h in holdings if h.get('symbol','').upper() == single_sym), None)
                if holding:
                    qty = float(holding.get('quantity') or 0)
                    avg = float(holding.get('avgCost') or 0)
                    val = qty * price
                    pct = ((price - avg)/avg*100) if avg else 0
                    return jsonify({"response": f"{single_sym} — {name} — is trading around ${price:.2f}, {chg:+.2f}% today. It's in the {sector} sector{f', with a {dy*100:.1f}% dividend yield' if dy>0.01 else ''}{f' and a P/E around {pe:.1f}' if pe else ''}.\n\nYou own {qty:.0f} shares at an average cost of ${avg:.2f}, so that position is about ${val:.0f} and you're {pct:+.1f}% on it.\n\nIn the context of your portfolio, {sector} is already {sectors.get(sector,0)/total_val*100 if total_val else 0:.0f}% of your total, so this is part of your larger {sector} exposure. What specifically did you want to think through about it — its performance, whether to add more, or how it compares to other {sector} names?"})
                else:
                    return jsonify({"response": f"{single_sym} — {name} — is around ${price:.2f}, {chg:+.2f}% today, in {sector}{f' with a {dy*100:.1f}% dividend yield' if dy>0.01 else ''}{f', P/E about {pe:.1f}' if pe else ''}.\n\nIt's not in your current holdings. Right now your portfolio is {top_pct:.0f}% {top_sector or 'concentrated in one area'}, so adding {single_sym} would { 'increase your concentration in ' + sector if sector==top_sector else 'add some diversification into ' + sector}. Do you see it as a long-term hold or more of a tactical idea?"})

        # 4. Functional but conversational
        if 'dividend' in lower:
            if not dividend_stocks:
                return jsonify({"response": f"You don't have any meaningful dividend payers yet — everything is more growth-oriented at the moment. You have {len(holdings)} holdings totaling ${total_val:.0f}. If income is something you want, names like JNJ, NEE, JPM, VYM, or SCHD are commonly held for yield, usually in the 2-4% range. Would you like me to show you how a dividend addition would change your income profile?"})
            dividend_stocks.sort(key=lambda x: x[1], reverse=True)
            avg_yield = sum(y for _,y,_,_,_,_ in dividend_stocks)/len(dividend_stocks)*100
            lines = [f"• {sym}: {y*100:.1f}% yield — {sector}, ${p:.0f} x {qty:.0f} shares = ${val:.0f}" for sym,y,sector,p,qty,val in dividend_stocks]
            total_div_val = sum(val for _,_,_,_,_,val in dividend_stocks)
            return jsonify({"response": f"You have {len(dividend_stocks)} positions paying a dividend, averaging {avg_yield:.1f}% yield. Together they're about ${total_div_val:.0f} of your portfolio.\n\n" + "\n".join(lines) + f"\n\nAt that average yield, that's roughly ${total_val * (avg_yield/100):.0f} a year in estimated dividend income, before taxes. That income component can help smooth things out when growth is volatile. Do you want to lean more into dividend or keep it as is?"})

        if any(x in lower for x in ['sector', 'allocation', 'breakdown', 'diversified', 'diversification']):
            if not sectors:
                return jsonify({"response": "Your sector data is still loading — give it a couple seconds and ask again. Once prices load I can show you exactly how much you have in each sector."})
            lines = []
            for sec, val in sorted(sectors.items(), key=lambda x: x[1], reverse=True):
                pct = val/total_val*100 if total_val else 0
                lines.append(f"• {sec}: {pct:.0f}% — about ${val:.0f}")
            diversification_note = ""
            if top_pct > 50:
                diversification_note = f" You are quite concentrated in {top_sector} at {top_pct:.0f}%, which means your portfolio will move a lot with that sector. That's not necessarily bad if you have high conviction, but it does increase single-sector risk."
            elif top_pct > 35:
                diversification_note = f" Your largest sector is {top_sector} at {top_pct:.0f}%. That's a meaningful tilt but not extreme — you still have exposure elsewhere."
            else:
                diversification_note = " Your allocation looks fairly balanced across sectors, which helps reduce single-sector risk."
            return jsonify({"response": f"Here's how your ${total_val:.0f} is allocated across {len(sectors)} sectors:\n\n" + "\n".join(lines) + f"\n\n{diversification_note}\n\nWould you like to talk about whether that mix matches what you want long-term?"})

        if any(x in lower for x in ['gainer', 'winner', 'best performer', 'doing best']):
            if not gainers:
                return jsonify({"response": f"Nothing is really up significantly at the moment — you have {len(losers)} positions down and {len(holdings)-len(gainers)-len(losers)} roughly flat. That's normal in a choppy market. Sometimes holding through is the strategy, sometimes it's worth asking if the thesis has changed for the losers. Want to walk through the down positions?"})
            gainers.sort(key=lambda x: x[1], reverse=True)
            lines = [f"• {s}: +{p:.1f}% — up about ${g:.0f} from your cost, now at ${pr:.0f} ({sector})" for s,p,g,pr,sector in gainers[:5]]
            return jsonify({"response": f"You have {len(gainers)} positions in the green right now:\n\n" + "\n".join(lines) + f"\n\nOverall you're {total_gain_pct:+.1f}% on the total portfolio. Nice to have some winners to balance things out."})

        if any(x in lower for x in ['loser', 'worst', 'down', 'losing', 'underperform']):
            if not losers:
                return jsonify({"response": "No real losers at the moment — everything is flat or up. That's a good place to be."})
            losers.sort(key=lambda x: x[1])
            lines = [f"• {s}: {p:.1f}% — down about ${g:.0f}, now at ${pr:.0f} ({sector})" for s,p,g,pr,sector in losers[:5]]
            return jsonify({"response": f"You have {len(losers)} positions down at the moment:\n\n" + "\n".join(lines) + f"\n\nIt's worth asking for each: has the underlying business changed, or is this just market volatility? If you still believe in the longer-term story, down periods can be uncomfortable but not necessarily a reason to act. If the thesis has changed, that's a different conversation. Want to talk through any of these specifically?"})

        if any(x in lower for x in ['performance','how am i doing','how are we doing','summary','portfolio summary']):
            return jsonify({"response": f"Here's a quick summary of where things stand:\n\nYou have {len(holdings)} holdings totaling about ${total_val:.0f}, with a total cost basis of about ${total_cost:.0f}. That puts you at {total_gain:+.0f} ({total_gain_pct:+.1f}%) overall.\n\n• Largest sector: {top_sector or 'Mixed'} at {top_pct:.0f}%\n• Winners: {len(gainers)}, Losers: {len(losers)}\n• Dividend payers: {len(dividend_stocks)}\n\nThat mix gives you a {'growth tilt' if etf_count < len(holdings)/2 else 'blend of individual stocks and ETFs'}. How are you feeling about that performance? Is there a part you want to dig into — risk, income, or specific positions?"})

        # 5. Explain concepts
        if any(x in lower for x in ['what is etf', 'what is dividend', 'what is pe', 'what is diversification', 'explain']):
            if 'dividend' in lower:
                return jsonify({"response": "A dividend is a portion of a company's profits paid out to shareholders, usually quarterly, expressed as a yield — annual dividend divided by price. For example, a 3% yield means you'd get about $30 a year for every $1000 invested, before taxes. Companies in sectors like utilities, healthcare, and financials tend to pay more consistent dividends, while tech often pays little or none and reinvests profits for growth."})
            if 'etf' in lower:
                return jsonify({"response": "An ETF — exchange-traded fund — is a basket of stocks that trades like a single stock. For example, SPY holds 500 large US companies, VYM holds high-dividend stocks. ETFs give you instant diversification without having to buy dozens of individual names. In your portfolio, ETFs are useful to balance out individual stock risk. You currently hold " + f"{etf_count} ETFs."})
            if 'pe' in lower or 'p/e' in lower:
                return jsonify({"response": "P/E — price-to-earnings ratio — compares a stock's price to its earnings per share. A higher P/E often means investors expect more future growth, a lower P/E can mean it's seen as cheaper or slower growing. It's useful but not the whole story — growth, sector, and profitability matter too."})
            return jsonify({"response": "Happy to explain — what concept did you want to dig into? I can cover dividends, ETFs, P/E ratios, diversification, sector allocation, or how to think about risk."})

        # 6. Delete / remove - still functional
        if any(x in lower for x in ['delete', 'remove']):
            import re as _re
            m = _re.search(r'\b([A-Z]{1,5})\b', prompt.upper())
            if m:
                sym = m.group(1)
                if sym not in ['DELETE','REMOVE','STOCKS','MY','THIS']:
                    return jsonify({"response": f"You want to remove {sym}? You can hover over {sym} in your holdings list and click the trash icon, or say 'yes, delete {sym}' and I'll remove it. You currently have {len(holdings)} holdings."})
            return jsonify({"response": "Which holding would you like to remove? Just say something like 'delete AAPL' and I'll handle it with a confirmation."})

        # 7. Default - open conversation, not confined
        # If we get here, it's an open-ended prompt — respond conversationally with context
        if len(prompt) < 5:
            return jsonify({"response": f"Could you say a bit more? {portfolio_context()} I can talk about your allocation, any ticker you're curious about, dividend income, or what's happening in the market generally."})

        # For any other market-related question, give a thoughtful response with portfolio context
        return jsonify({"response": f"That's an interesting question.\n\n{portfolio_context()} Your largest exposure is {top_sector or 'spread across sectors'} at {top_pct:.0f}%, and you're {total_gain_pct:+.1f}% overall.\n\nCould you share a bit more about what you're thinking? For example:\n• Are you asking about a specific ticker or sector?\n• Are you thinking about risk, income, or growth?\n• Or are you looking for a take on broader market conditions?\n\nI can discuss any of those with your actual numbers in mind — just let me know what angle you're most interested in. I'm here to talk through it, not just give you a preset list."})

    except Exception as e:
        print(f"chat error {e}")
        traceback.print_exc()
        return jsonify({"response": "I had a brief hiccup pulling your data, but I'm back now. Your holdings are still safe. What would you like to talk about — your allocation, a specific ticker, or the market more broadly?"}), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    try:
        if path.startswith('api/'):
            return jsonify({"error": "Not Found"}), 404
        if path and os.path.exists(path):
            return send_from_directory('.', path)
        if os.path.exists('index.html'):
            return send_from_directory('.', 'index.html')
        return jsonify({"ok": True, "message": "tickr v12 conversational"})
    except Exception as e:
        print(f"serve error {e}")
        return jsonify({"error": "serve error"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"tickr v12 conversational starting on {port}")
    app.run(host='0.0.0.0', port=port)
