from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="PPTJetX", version="0.1.0", docs_url="/api-docs")

# Include Transcriber API
try:
    from .transcriber import router as transcriber_router
    app.include_router(transcriber_router)  # /api/transcribe and /api/health
except Exception as e:
    import logging
    logging.getLogger("pptjetx").warning(f"Transcriber not loaded: {e}")

@app.get("/transcriber", response_class=HTMLResponse, include_in_schema=False)
async def transcriber_page() -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html lang='en'>
    <head>
      <meta charset='UTF-8' />
      <title>Transcriber  PPTJetX</title>
      <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-6405372608012962" crossorigin="anonymous"></script>
      <style>
        body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; }
        .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; }
        label { display:block; margin: 10px 0; }
        input[type=text] { width:100%; padding:10px; }
        textarea { width:100%; min-height: 260px; }
        button { padding: 10px 16px; }
        .muted { color:#666; font-size: 0.95rem; }
        .row { display:flex; gap: 12px; align-items: center; }
      </style>
    </head>
    <body>
      <h1>Transcribe YouTube</h1>

      <ins class="adsbygoogle" style="display:block" data-ad-client="ca-pub-6405372608012962" data-ad-slot="9445777349" data-ad-format="auto" data-full-width-responsive="true"></ins>
      <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>

      <div class='card' style='margin-top:12px;'>
        <label>YouTube URL *</label>
        <input id='ytUrl' type='text' placeholder='https://www.youtube.com/watch?v=XXXXXXXXXXX' />
        <label><input id='prefer' type='checkbox' checked /> Prefer official captions (faster/free)</label>
        <label><input id='authz' type='checkbox' /> I own/am authorized to process this video *</label>
        <button id='goBtn'>Convert to Text</button>
        <span id='status' class='muted' style='margin-left:8px;'></span>
      </div>

      <div id='result' style='display:none; margin-top:16px;' class='card'>
        <p><strong>Word count:</strong> <span id='wc'>0</span></p>
        <textarea id='out' readonly></textarea>
        <div class='row' style='margin-top:8px;'>
          <button id='copyBtn'>Copy</button>
          <button id='downloadBtn'>Download .txt</button>
          <button id='resetBtn'>Start another</button>
        </div>
      </div>

      <div style='margin-top:16px;'>
        <ins class="adsbygoogle" style="display:block" data-ad-client="ca-pub-6405372608012962" data-ad-slot="9445777349" data-ad-format="auto" data-full-width-responsive="true"></ins>
        <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
      </div>

      <script>
      const urlEl = document.getElementById('ytUrl');
      const preferEl = document.getElementById('prefer');
      const authzEl = document.getElementById('authz');
      const goBtn = document.getElementById('goBtn');
      const statusEl = document.getElementById('status');
      const resDiv = document.getElementById('result');
      const outEl = document.getElementById('out');
      const wcEl = document.getElementById('wc');
      const copyBtn = document.getElementById('copyBtn');
      const dlBtn = document.getElementById('downloadBtn');
      const resetBtn = document.getElementById('resetBtn');

      function validUrl(u){ try { new URL(u); return /youtu\.be|youtube\.com/.test(u); } catch(_) { return false; } }

      async function transcribe(){
        const url = urlEl.value.trim();
        if (!validUrl(url)) { statusEl.textContent = 'Enter a valid YouTube URL.'; return; }
        if (!authzEl.checked) { statusEl.textContent = 'Please confirm authorization.'; return; }
        goBtn.disabled = true; statusEl.textContent = 'Transcribing';
        resDiv.style.display = 'none'; outEl.value = ''; wcEl.textContent = '0';
        try {
          const resp = await fetch('/api/transcribe', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, preferCaptions: preferEl.checked, authorized: true })
          });
          const payload = await resp.json().catch(() => ({}));
          if (!resp.ok) {
            if (resp.status === 429) { throw new Error('Too many requests — please wait a bit.'); }
            throw new Error(payload.detail || 'Failed to transcribe');
          }
          outEl.value = payload.text || '';
          wcEl.textContent = payload.wordCount || 0;
          resDiv.style.display = 'block';
          statusEl.textContent = 'Done';
        } catch (e) {
          statusEl.textContent = 'Error: ' + e.message;
        } finally {
          goBtn.disabled = false;
        }
      }
      goBtn.addEventListener('click', transcribe);

      copyBtn.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(outEl.value); statusEl.textContent = 'Copied!'; } catch(_) {}
      });
      dlBtn.addEventListener('click', () => {
        const blob = new Blob([outEl.value], { type: 'text/plain;charset=utf-8' });
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'transcript.txt'; a.click();
        URL.revokeObjectURL(a.href);
      });
      resetBtn.addEventListener('click', () => { outEl.value=''; wcEl.textContent='0'; resDiv.style.display='none'; statusEl.textContent=''; urlEl.value=''; authzEl.checked=false; preferEl.checked=true; });
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)
