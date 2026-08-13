// DROP-IN LOGIN FOR FLOW - add to index.html before </body>
// This adds a login button + modal that uses your existing /api/auth
(function(){
  const API = ''; // same origin
  const style = document.createElement('style');
  style.textContent = `
    #flow-login-btn { height:32px; padding:0 16px; border-radius:9999px; background:#0a0a0a; color:white; font-size:13px; font-weight:500; border:1px solid rgba(0,0,0,0.1); cursor:pointer; }
    .dark #flow-login-btn { background:white; color:#18181b; }
    #flow-login-modal { position:fixed; inset:0; z-index:100; display:none; align-items:center; justify-content:center; }
    #flow-login-modal.open { display:flex; }
    #flow-login-backdrop { position:absolute; inset:0; background:rgba(0,0,0,0.3); backdrop-filter:blur(4px); }
    #flow-login-card { position:relative; width:360px; max-width:90vw; background:#fcfbf8; border:1px solid rgba(0,0,0,0.1); border-radius:20px; padding:24px; box-shadow:0 20px 60px rgba(0,0,0,0.2); }
    .dark #flow-login-card { background:#141414; border-color:rgba(255,255,255,0.1); color:white; }
    #flow-login-card input { width:100%; height:40px; border-radius:9999px; border:1px solid rgba(0,0,0,0.1); background:white; padding:0 16px; margin-top:8px; font-size:14px; outline:none; }
    .dark #flow-login-card input { background:#27272a; border-color:rgba(255,255,255,0.1); color:white; }
    #flow-login-card label { font-size:11px; text-transform:uppercase; letter-spacing:0.1em; color:#71717a; }
  `;
  document.head.appendChild(style);

  function getToken(){ return localStorage.getItem('flow_token'); }
  function setToken(t){ if(t) localStorage.setItem('flow_token', t); else localStorage.removeItem('flow_token'); }
  
  function injectButton(){
    const header = document.querySelector('header div.flex.items-center.gap-2.min-w-0');
    if(!header || document.getElementById('flow-login-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'flow-login-btn';
    btn.textContent = getToken() ? 'Account' : 'Log In';
    btn.onclick = () => openModal();
    header.prepend(btn);
    
    // update text if logged in
    const token = getToken();
    if(token){
      fetch('/api/me', { headers: { 'X-Auth-Token': token }})
        .then(r=>r.json())
        .then(j=>{
          if(j.username) btn.textContent = j.username;
          else { setToken(null); btn.textContent = 'Log In'; }
        }).catch(()=>{});
    }
  }

  function createModal(){
    if(document.getElementById('flow-login-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'flow-login-modal';
    modal.innerHTML = `
      <div id="flow-login-backdrop"></div>
      <div id="flow-login-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
          <div style="font-weight:600">Flow Account</div>
          <button id="flow-login-close" style="width:28px;height:28px;border-radius:9999px;border:1px solid rgba(0,0,0,0.1);background:white;cursor:pointer">×</button>
        </div>
        <div id="flow-login-form">
          <div style="margin-bottom:16px"><label>Username</label><input id="flow-u" placeholder="asher" autocomplete="username"/></div>
          <div style="margin-bottom:16px"><label>Password</label><input id="flow-p" type="password" placeholder="••••••••" autocomplete="current-password"/></div>
          <div id="flow-login-msg" style="font-size:12px;color:#71717a;margin-bottom:12px;min-height:16px"></div>
          <div style="display:flex;gap:8px">
            <button id="flow-do-login" style="flex:1;height:44px;border-radius:9999px;background:#0a0a0a;color:white;font-weight:500;border:0;cursor:pointer">Log In / Sign Up</button>
          </div>
          <div style="margin-top:12px;font-size:11px;color:#a1a1aa;text-align:center">If account doesn't exist, we'll create it. Portfolio saves to Postgres.</div>
          <button id="flow-do-logout" style="display:none;margin-top:12px;width:100%;height:36px;border-radius:9999px;border:1px solid rgba(0,0,0,0.1);background:transparent;cursor:pointer;font-size:12px">Log Out</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    document.getElementById('flow-login-backdrop').onclick = closeModal;
    document.getElementById('flow-login-close').onclick = closeModal;
    document.getElementById('flow-do-login').onclick = doAuth;
    document.getElementById('flow-do-logout').onclick = doLogout;
  }

  function openModal(){
    createModal();
    const token = getToken();
    document.getElementById('flow-login-modal').classList.add('open');
    if(token){
      document.getElementById('flow-login-msg').textContent = 'Logged in. Your holdings save automatically.';
      document.getElementById('flow-do-logout').style.display = 'block';
      document.getElementById('flow-do-login').textContent = 'Continue';
    }
  }
  function closeModal(){
    const m = document.getElementById('flow-login-modal');
    if(m) m.classList.remove('open');
  }
  async function doAuth(){
    const u = document.getElementById('flow-u').value.trim();
    const p = document.getElementById('flow-p').value;
    const msg = document.getElementById('flow-login-msg');
    if(!u || !p){ msg.textContent = 'Enter username + password'; return; }
    msg.textContent = 'Authenticating...';
    try{
      const r = await fetch('/api/auth', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, password:p}) });
      const j = await r.json();
      if(!r.ok){ msg.textContent = j.error || 'Auth failed'; return; }
      setToken(j.token);
      msg.textContent = 'Logged in as ' + j.username + ' ✓';
      const btn = document.getElementById('flow-login-btn');
      if(btn) btn.textContent = j.username;
      setTimeout(closeModal, 800);
      location.reload();
    }catch(e){ msg.textContent = 'Network error: '+e; }
  }
  function doLogout(){
    setToken(null);
    closeModal();
    const btn = document.getElementById('flow-login-btn');
    if(btn) btn.textContent = 'Log In';
    location.reload();
  }

  // monkey-patch fetch to add token automatically
  const origFetch = window.fetch;
  window.fetch = function(url, opts={}){
    opts.headers = opts.headers || {};
    const t = getToken();
    if(t && (typeof url === 'string') && (url.includes('/api/portfolio') || url.includes('/api/me'))){
      if(opts.headers instanceof Headers) opts.headers.set('X-Auth-Token', t);
      else opts.headers['X-Auth-Token'] = t;
    }
    return origFetch(url, opts);
  };

  setInterval(injectButton, 1000);
  injectButton();
  createModal();
})();
