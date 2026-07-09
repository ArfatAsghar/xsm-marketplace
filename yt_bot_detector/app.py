"""
app.py  ─  YouTube Bot Detector  ─  Flask Web Application
"""

import os
import json
import threading
from flask import Flask, render_template_string, request, jsonify

from analyzer import analyze

app = Flask(__name__)

# ── train model on first launch if not present ───────────────────────────────
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "model_meta.json")

def _ensure_model():
    try:
        from train_model import train as train_model
        if not os.path.exists(MODEL_PATH):
            print("[APP] Model not found — training now …")
            train_model()
            print("[APP] Model ready ✅")
    except Exception as e:
        print(f"[APP] Could not run automatic training fallback: {e}")

threading.Thread(target=_ensure_model, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  HTML TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>YT Bot Detector — Fake Subscriber Scanner</title>
<meta name="description" content="Detect fake subscribers, inflated views and bot engagement on any YouTube channel using real YouTube API data and XGBoost machine learning."/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet"/>
<style>
  /* ── CSS RESET & TOKENS ─────────────────────────────────────────────────── */
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#080810;
    --bg2:#0e0e1a;
    --bg3:#13131f;
    --border:#1e1e30;
    --border2:#2a2a40;
    --text:#e2e2f0;
    --text2:#8888aa;
    --text3:#555570;
    --accent:#7c3aed;
    --accent2:#a855f7;
    --danger:#ef4444;
    --warn:#f97316;
    --caution:#f59e0b;
    --ok:#22c55e;
    --ok2:#84cc16;
    --glow:rgba(124,58,237,.35);
    --glow2:rgba(168,85,247,.2);
    --r:12px;
    --r2:8px;
    --shadow:0 8px 40px rgba(0,0,0,.6);
  }

  html{scroll-behavior:smooth}
  body{
    background:var(--bg);
    color:var(--text);
    font-family:'Inter',sans-serif;
    min-height:100vh;
    overflow-x:hidden;
  }

  /* ── NOISE OVERLAY ──────────────────────────────────────────────────────── */
  body::before{
    content:'';
    position:fixed;inset:0;
    background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events:none;z-index:0;opacity:.6;
  }

  /* ── GRADIENT ORBS ──────────────────────────────────────────────────────── */
  .orb{position:fixed;border-radius:50%;filter:blur(80px);pointer-events:none;z-index:0}
  .orb-1{width:600px;height:600px;top:-200px;left:-150px;background:radial-gradient(circle,rgba(124,58,237,.15),transparent 70%)}
  .orb-2{width:500px;height:500px;bottom:-150px;right:-100px;background:radial-gradient(circle,rgba(239,68,68,.10),transparent 70%)}

  /* ── LAYOUT ─────────────────────────────────────────────────────────────── */
  .container{position:relative;z-index:1;max-width:900px;margin:0 auto;padding:2rem 1.5rem 4rem}

  /* ── HEADER ─────────────────────────────────────────────────────────────── */
  header{text-align:center;padding:4rem 0 3rem}
  .logo-badge{
    display:inline-flex;align-items:center;gap:.6rem;
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    color:#fff;font-size:.75rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
    padding:.4rem 1rem;border-radius:999px;margin-bottom:1.5rem;
    box-shadow:0 0 20px var(--glow);
  }
  .logo-badge svg{width:16px;height:16px;fill:#fff}
  h1{
    font-size:clamp(2rem,5vw,3.2rem);font-weight:900;line-height:1.1;
    background:linear-gradient(135deg,#fff 30%,var(--accent2) 70%,var(--danger) 100%);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
    margin-bottom:1rem;
  }
  .subtitle{color:var(--text2);font-size:1.05rem;max-width:540px;margin:0 auto;line-height:1.7}

  /* ── SEARCH CARD ─────────────────────────────────────────────────────────── */
  .search-card{
    background:linear-gradient(135deg,rgba(124,58,237,.08),rgba(14,14,26,.6));
    border:1px solid var(--border2);
    border-radius:var(--r);
    padding:2rem;
    margin-bottom:2.5rem;
    box-shadow:var(--shadow),0 0 0 1px rgba(124,58,237,.08);
    backdrop-filter:blur(12px);
  }
  .input-row{display:flex;gap:.75rem;flex-wrap:wrap}
  .input-wrap{flex:1;min-width:260px;position:relative}
  .input-wrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:var(--text3);pointer-events:none}
  input[type=text]{
    width:100%;background:var(--bg3);border:1px solid var(--border2);
    color:var(--text);font-family:inherit;font-size:.95rem;
    padding:.85rem 1rem .85rem 2.75rem;border-radius:var(--r2);
    outline:none;transition:border-color .2s,box-shadow .2s;
  }
  input[type=text]:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--glow)}
  input[type=text]::placeholder{color:var(--text3)}

  .api-toggle{margin:.75rem 0 0;display:flex;align-items:center;gap:.5rem;cursor:pointer}
  .api-toggle input{accent-color:var(--accent);width:16px;height:16px}
  .api-toggle span{font-size:.85rem;color:var(--text2)}
  #apiKeyWrap{display:none;margin-top:.75rem}

  .btn-analyze{
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    color:#fff;font-family:inherit;font-weight:700;font-size:.95rem;
    padding:.85rem 2rem;border:none;border-radius:var(--r2);
    cursor:pointer;white-space:nowrap;transition:opacity .2s,transform .15s,box-shadow .2s;
    box-shadow:0 4px 20px var(--glow);
  }
  .btn-analyze:hover{opacity:.9;transform:translateY(-1px);box-shadow:0 8px 32px var(--glow)}
  .btn-analyze:active{transform:translateY(0)}
  .btn-analyze:disabled{opacity:.5;cursor:not-allowed;transform:none}

  .hint-row{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}
  .hint-chip{
    background:var(--bg3);border:1px solid var(--border);
    color:var(--text2);font-size:.78rem;padding:.3rem .75rem;
    border-radius:999px;cursor:pointer;transition:border-color .2s,color .2s;
  }
  .hint-chip:hover{border-color:var(--accent);color:var(--text)}

  /* ── STATUS / LOADER ─────────────────────────────────────────────────────── */
  #statusMsg{
    text-align:center;font-size:.9rem;color:var(--text2);
    min-height:2rem;transition:opacity .3s;
  }
  .spinner{
    display:inline-block;width:18px;height:18px;
    border:2.5px solid rgba(124,58,237,.3);border-top-color:var(--accent2);
    border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:.4rem;
  }
  @keyframes spin{to{transform:rotate(360deg)}}

  /* ── RESULT PANEL ────────────────────────────────────────────────────────── */
  #resultPanel{display:none;animation:fadeUp .5s ease both}
  @keyframes fadeUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}

  /* ── VERDICT HERO ────────────────────────────────────────────────────────── */
  .verdict-hero{
    border-radius:var(--r);border:1px solid var(--border2);
    padding:2rem;margin-bottom:1.5rem;
    background:linear-gradient(135deg,var(--bg2),var(--bg3));
    display:flex;flex-wrap:wrap;gap:1.5rem;align-items:center;
  }
  .channel-thumb{width:72px;height:72px;border-radius:50%;object-fit:cover;background:var(--bg3);border:2px solid var(--border2)}
  .thumb-placeholder{width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:1.8rem;font-weight:900;color:#fff;flex-shrink:0}
  .channel-info{flex:1;min-width:180px}
  .channel-info h2{font-size:1.3rem;font-weight:700;margin-bottom:.25rem}
  .channel-info .handle{color:var(--text2);font-size:.9rem;margin-bottom:.5rem}
  .channel-stats{display:flex;flex-wrap:wrap;gap:.75rem;margin-top:.5rem}
  .stat-pill{
    background:var(--bg);border:1px solid var(--border);
    font-size:.78rem;padding:.25rem .65rem;border-radius:999px;color:var(--text2);
  }
  .stat-pill b{color:var(--text)}

  .verdict-score-wrap{text-align:center;min-width:130px}
  .verdict-label{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700;margin-bottom:.4rem}
  .verdict-ring{position:relative;width:100px;height:100px;margin:0 auto .5rem}
  .verdict-ring svg{transform:rotate(-90deg)}
  .verdict-ring-text{
    position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
  }
  .verdict-pct{font-size:1.6rem;font-weight:900;line-height:1}
  .verdict-sub{font-size:.6rem;color:var(--text2);margin-top:.15rem;text-transform:uppercase;letter-spacing:.08em}
  .verdict-badge{
    display:inline-block;font-size:.8rem;font-weight:700;
    padding:.3rem .8rem;border-radius:999px;margin-top:.4rem;
  }

  /* ── GRID STATS ──────────────────────────────────────────────────────────── */
  .meta-grid{
    display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));
    gap:1rem;margin-bottom:1.5rem;
  }
  .meta-card{
    background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);
    padding:1rem;text-align:center;transition:border-color .2s;
  }
  .meta-card:hover{border-color:var(--border2)}
  .meta-card .mc-val{font-size:1.3rem;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--accent2)}
  .meta-card .mc-lbl{font-size:.72rem;color:var(--text3);margin-top:.2rem;text-transform:uppercase;letter-spacing:.08em}

  /* ── SIGNAL COLUMNS ──────────────────────────────────────────────────────── */
  .signals-row{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem}
  @media(max-width:640px){.signals-row{grid-template-columns:1fr}}
  .signal-col{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r2);padding:1.25rem}
  .signal-col h3{font-size:.8rem;letter-spacing:.1em;text-transform:uppercase;font-weight:700;margin-bottom:1rem;display:flex;align-items:center;gap:.4rem}
  .signal-col.red h3{color:var(--danger)}
  .signal-col.green h3{color:var(--ok)}

  .signal-item{margin-bottom:.85rem;padding-bottom:.85rem;border-bottom:1px solid var(--border)}
  .signal-item:last-child{margin-bottom:0;padding-bottom:0;border-bottom:none}
  .signal-name{font-size:.82rem;font-weight:600;margin-bottom:.15rem}
  .signal-desc{font-size:.75rem;color:var(--text2);line-height:1.5}
  .signal-bar-wrap{margin-top:.4rem;height:4px;background:var(--bg);border-radius:2px}
  .signal-bar{height:100%;border-radius:2px;transition:width .6s ease}

  /* ── FULL FEATURE TABLE ──────────────────────────────────────────────────── */
  .feature-section{margin-bottom:2rem}
  .section-title{
    font-size:.8rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
    color:var(--text2);margin-bottom:.75rem;display:flex;align-items:center;gap:.5rem;
  }
  .section-title::after{content:'';flex:1;height:1px;background:var(--border)}
  details summary{cursor:pointer;user-select:none;outline:none}
  details summary::-webkit-details-marker{display:none}
  .feature-table-wrap{overflow-x:auto;margin-top:.75rem}
  table{width:100%;border-collapse:collapse;font-size:.82rem}
  thead th{
    background:var(--bg3);color:var(--text2);font-weight:600;font-size:.75rem;
    text-transform:uppercase;letter-spacing:.07em;padding:.6rem .75rem;text-align:left;
    border-bottom:1px solid var(--border);
  }
  tbody tr{border-bottom:1px solid var(--border);transition:background .15s}
  tbody tr:hover{background:var(--bg3)}
  tbody td{padding:.55rem .75rem;color:var(--text);vertical-align:middle}
  .shap-positive{color:var(--danger);font-weight:600}
  .shap-negative{color:var(--ok);font-weight:600}
  .shap-neutral{color:var(--text2)}
  .mono{font-family:'JetBrains Mono',monospace;font-size:.78rem}

  /* ── FOOTER ──────────────────────────────────────────────────────────────── */
  footer{text-align:center;padding:3rem 0 1rem;color:var(--text3);font-size:.8rem;line-height:1.8}
  footer a{color:var(--accent2);text-decoration:none}

  /* ── ERROR TOAST ─────────────────────────────────────────────────────────── */
  .error-box{
    background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.35);
    border-radius:var(--r2);padding:1rem 1.25rem;color:var(--danger);
    display:flex;align-items:flex-start;gap:.7rem;margin-bottom:1.5rem;
  }

  /* ── SCROLLBAR ───────────────────────────────────────────────────────────── */
  ::-webkit-scrollbar{width:6px;height:6px}
  ::-webkit-scrollbar-track{background:var(--bg)}
  ::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
  ::-webkit-scrollbar-thumb:hover{background:var(--accent)}
</style>
</head>
<body>
<div class="orb orb-1"></div>
<div class="orb orb-2"></div>

<div class="container">
  <!-- HEADER -->
  <header>
    <div class="logo-badge">
      <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"/></svg>
      YouTube Bot Detector
    </div>
    <h1>Detect Fake Subscribers<br>&amp; Bot Engagement</h1>
    <p class="subtitle">Reads <strong>real data</strong> directly from the YouTube API. Never trusts seller claims. Powered by XGBoost + SHAP explainability.</p>
  </header>

  <!-- SEARCH CARD -->
  <div class="search-card">
    <div class="input-row">
      <div class="input-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input id="channelInput" type="text"
          placeholder="@channel, youtube.com/channel/UC…, or any channel name"
          autocomplete="off" spellcheck="false"/>
      </div>
      <button class="btn-analyze" id="analyzeBtn" onclick="runAnalysis()">
        Analyze Channel
      </button>
    </div>

    <label class="api-toggle">
      <input type="checkbox" id="apiKeyToggle" onchange="toggleApiKey()"/>
      <span>Use my YouTube API key (for live data)</span>
    </label>
    <div id="apiKeyWrap">
      <div class="input-wrap" style="margin-top:.4rem;min-width:unset;max-width:420px">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <input id="apiKeyInput" type="text" placeholder="AIza…" autocomplete="off" spellcheck="false"/>
      </div>
    </div>

    <div class="hint-row">
      <span style="font-size:.78rem;color:var(--text3);align-self:center">Try:</span>
      <span class="hint-chip" onclick="setChannel('@MrBeast')">@MrBeast</span>
      <span class="hint-chip" onclick="setChannel('@PewDiePie')">@PewDiePie</span>
      <span class="hint-chip" onclick="setChannel('botchannel99')">botchannel99</span>
      <span class="hint-chip" onclick="setChannel('fakesubs2024')">fakesubs2024</span>
    </div>
  </div>

  <!-- STATUS -->
  <div id="statusMsg"></div>

  <!-- RESULT -->
  <div id="resultPanel">
    <div id="errorBox" style="display:none" class="error-box">
      <svg style="width:18px;height:18px;flex-shrink:0;margin-top:1px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/></svg>
      <span id="errorText"></span>
    </div>
    <div id="resultContent"></div>
  </div>

  <!-- FOOTER -->
  <footer>
    <p>YouTube Bot Detector &mdash; powered by XGBoost + SHAP &bull; Data via YouTube Data API v3</p>
    <p style="margin-top:.25rem">No API key? The tool generates deterministic mock data so you can explore the full analysis.</p>
  </footer>
</div>

<script>
function setChannel(val) {
  document.getElementById('channelInput').value = val;
  document.getElementById('channelInput').focus();
}

function toggleApiKey() {
  const wrap = document.getElementById('apiKeyWrap');
  wrap.style.display = document.getElementById('apiKeyToggle').checked ? 'block' : 'none';
}

document.getElementById('channelInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') runAnalysis();
});

function fmt(n) {
  if (n === undefined || n === null) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

async function runAnalysis() {
  const channel = document.getElementById('channelInput').value.trim();
  if (!channel) {
    document.getElementById('statusMsg').innerHTML = '⚠️ Please enter a channel handle, URL, or name.';
    return;
  }

  const apiKey = document.getElementById('apiKeyToggle').checked
    ? document.getElementById('apiKeyInput').value.trim()
    : '';

  const btn = document.getElementById('analyzeBtn');
  btn.disabled = true;
  document.getElementById('statusMsg').innerHTML = '<span class="spinner"></span> Fetching channel data…';
  document.getElementById('resultPanel').style.display = 'none';

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ channel, api_key: apiKey })
    });
    const data = await res.json();
    document.getElementById('statusMsg').innerHTML = '';
    renderResult(data);
  } catch(e) {
    document.getElementById('statusMsg').innerHTML = '❌ Request failed. Is the server running?';
  } finally {
    btn.disabled = false;
  }
}

function renderResult(data) {
  const panel = document.getElementById('resultPanel');
  const errBox = document.getElementById('errorBox');
  const content = document.getElementById('resultContent');

  panel.style.display = 'block';
  panel.scrollIntoView({behavior:'smooth', block:'start'});

  if (data.error) {
    errBox.style.display = 'flex';
    document.getElementById('errorText').textContent = data.error;
    content.innerHTML = '';
    return;
  }

  errBox.style.display = 'none';
  const ch = data.channel;
  const v  = data.verdict;

  // ── verdict ring ──────────────────────────────────────────────────────────
  const authenticity = v.score;
  const botPct = Math.round(v.prob_bot * 100);
  const circumference = 2 * Math.PI * 42;
  const offset = circumference * (1 - authenticity / 100);
  const ringColor = v.color;

  const thumbHTML = ch.thumbnail
    ? `<img class="channel-thumb" src="${ch.thumbnail}" alt="${ch.title}" onerror="this.outerHTML='<div class=thumb-placeholder>${ch.title[0]||'?'}</div>'" />`
    : `<div class="thumb-placeholder">${(ch.title||'?')[0]}</div>`;

  const verdictHeroHTML = `
  <div class="verdict-hero">
    ${thumbHTML}
    <div class="channel-info">
      <h2>${esc(ch.title)}</h2>
      <div class="handle">${esc(ch.handle) || '—'} &nbsp;·&nbsp; ${esc(ch.country||'')}</div>
      <div class="channel-stats">
        <span class="stat-pill">👥 <b>${fmt(ch.subscriber_count)}</b> subs</span>
        <span class="stat-pill">👁 <b>${fmt(ch.total_views)}</b> views</span>
        <span class="stat-pill">🎬 <b>${fmt(ch.video_count)}</b> videos</span>
        <span class="stat-pill">📊 ${data.meta.n_videos_analyzed} analyzed</span>
        <span class="stat-pill">💬 ${data.meta.n_comments_analyzed} comments</span>
      </div>
    </div>
    <div class="verdict-score-wrap">
      <div class="verdict-label" style="color:${ringColor}">${v.icon} ${v.label}</div>
      <div class="verdict-ring">
        <svg viewBox="0 0 100 100" width="100" height="100">
          <circle cx="50" cy="50" r="42" fill="none" stroke="#1e1e30" stroke-width="8"/>
          <circle cx="50" cy="50" r="42" fill="none" stroke="${ringColor}" stroke-width="8"
            stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
            stroke-linecap="round" style="transition:stroke-dashoffset 1s ease"/>
        </svg>
        <div class="verdict-ring-text">
          <span class="verdict-pct" style="color:${ringColor}">${authenticity}%</span>
          <span class="verdict-sub">authentic</span>
        </div>
      </div>
      <div class="verdict-badge" style="background:${ringColor}22;color:${ringColor};border:1px solid ${ringColor}44">
        ${botPct}% bot risk
      </div>
    </div>
  </div>`;

  // ── signals ───────────────────────────────────────────────────────────────
  const redFlags = data.top_red_flags || [];
  const cleanSigs = data.top_clean_signals || [];

  function signalCard(item, isRed) {
    const shapAbs = Math.abs(item.shap);
    const barPct = Math.min(100, shapAbs * 600);
    const barColor = isRed ? 'var(--danger)' : 'var(--ok)';
    return `
    <div class="signal-item">
      <div class="signal-name">${esc(item.label)}</div>
      <div class="signal-desc">${esc(item.description)}</div>
      <div class="signal-bar-wrap">
        <div class="signal-bar" style="width:${barPct}%;background:${barColor}"></div>
      </div>
    </div>`;
  }

  const signalHTML = `
  <div class="signals-row">
    <div class="signal-col red">
      <h3>🚨 Bot Signals</h3>
      ${redFlags.length ? redFlags.map(f => signalCard(f, true)).join('') : '<p style="color:var(--text3);font-size:.85rem">No significant bot signals detected.</p>'}
    </div>
    <div class="signal-col green">
      <h3>✅ Clean Signals</h3>
      ${cleanSigs.length ? cleanSigs.map(f => signalCard(f, false)).join('') : '<p style="color:var(--text3);font-size:.85rem">No significant clean signals detected.</p>'}
    </div>
  </div>`;

  // ── full feature table ────────────────────────────────────────────────────
  const allFeats = data.features || [];
  const tableRows = allFeats.map(f => {
    const shapClass = f.shap > 0.005 ? 'shap-positive' : f.shap < -0.005 ? 'shap-negative' : 'shap-neutral';
    const shapSign  = f.shap > 0 ? '+' : '';
    return `<tr>
      <td>${esc(f.label)}</td>
      <td class="mono">${typeof f.value === 'number' ? f.value.toFixed(4) : f.value}</td>
      <td class="${shapClass} mono">${shapSign}${f.shap.toFixed(4)}</td>
      <td style="font-size:.75rem;color:var(--text2)">${esc(f.description)}</td>
    </tr>`;
  }).join('');

  const tableHTML = `
  <div class="feature-section">
    <details>
      <summary class="section-title" style="display:flex;align-items:center;gap:.5rem;cursor:pointer">
        <span style="color:var(--text2);font-size:.8rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase">
          🔬 Full Feature Breakdown (24 signals)
        </span>
        <span style="flex:1;height:1px;background:var(--border)"></span>
        <span style="color:var(--accent2);font-size:.75rem">▼ expand</span>
      </summary>
      <div class="feature-table-wrap">
        <table>
          <thead><tr>
            <th>Feature</th><th>Value</th><th>SHAP Impact</th><th>Description</th>
          </tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
    </details>
  </div>`;

  content.innerHTML = verdictHeroHTML + signalHTML + tableHTML;
}

function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    body    = request.get_json(force=True, silent=True) or {}
    channel = (body.get("channel") or "").strip()
    api_key = (body.get("api_key") or "").strip() or None

    if not channel:
        return jsonify({"error": "channel field is required"}), 400

    result = analyze(channel, api_key=api_key)
    return jsonify(result)


@app.route("/api/model-status")
def model_status():
    ready = os.path.exists(MODEL_PATH)
    return jsonify({"model_ready": ready})


if __name__ == "__main__":
    app.run(debug=False, port=5050, host="0.0.0.0")
