import os, io, csv, json, base64, re, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import requests
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "CHANGE-ME-IN-VERCEL")
BKK = ZoneInfo("Asia/Bangkok")

GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_CSV_PATH = os.environ.get("GITHUB_CSV_PATH", "data/diagnoses.csv")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
DOCTOR_PIN = os.environ.get("DOCTOR_PIN", "")
PUBLIC_HEALTH_PIN = os.environ.get("PUBLIC_HEALTH_PIN", "")

DIAGNOSIS_MAP = {
    "cold": [["J00", "Acute nasopharyngitis [common cold]"], ["J06.9", "Acute upper respiratory infection, unspecified"]],
    "pharyngitis": [["J02.9", "Acute pharyngitis, unspecified"], ["J02.0", "Streptococcal pharyngitis"]],
    "diarrhea": [["A09", "Infectious gastroenteritis and colitis, unspecified"], ["K52.9", "Noninfective gastroenteritis and colitis, unspecified"]],
    "strain": [["T14.6", "Injury of muscles and tendons of unspecified body region"], ["S39.012A", "Strain of muscle/fascia/tendon of lower back, initial encounter"]],
    "sprain": [["T14.3", "Dislocation, sprain and strain of unspecified body region"], ["S93.409A", "Sprain of unspecified ligament of unspecified ankle, initial encounter"]],
    "pain": [["R52", "Pain, unspecified"], ["M79.10", "Myalgia, unspecified site"]],
    "headache": [["R51.9", "Headache, unspecified"], ["G44.209", "Tension-type headache, unspecified"]],
    "rash": [["R21", "Rash and other nonspecific skin eruption"], ["L30.9", "Dermatitis, unspecified"]],
    "influenza": [["J11.1", "Influenza with respiratory manifestations, virus not identified"], ["J10.1", "Influenza with respiratory manifestations, identified virus"]],
    "dengue": [["A90", "Dengue fever [classical dengue]"], ["A91", "Dengue hemorrhagic fever"]],
    "conjunctivitis": [["H10.9", "Conjunctivitis, unspecified"], ["B30.9", "Viral conjunctivitis, unspecified"]],
    "hand-foot-mouth": [["B08.4", "Enteroviral vesicular stomatitis with exanthem"]],
}

# SAMPLE ONLY — replace with the officially approved current Thai DDC/local surveillance list.
SURVEILLANCE_CODES = {
    "A09": "Acute infectious diarrhea / gastroenteritis",
    "A90": "Dengue fever",
    "A91": "Dengue hemorrhagic fever",
    "J10.1": "Influenza, identified virus",
    "J11.1": "Influenza-like illness / influenza",
    "B08.4": "Hand-foot-mouth disease",
    "B30.9": "Viral conjunctivitis",
}

FIELDS = ["timestamp_bkk", "hn", "keyword", "icd10", "diagnosis", "surveillance", "surveillance_name"]

BASE_HTML = '''
<!doctype html><html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{{ title }}</title>
<style>
:root{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#17202a}body{margin:0;background:#f5f7fa}.wrap{max-width:920px;margin:auto;padding:16px}.card{background:white;border-radius:16px;padding:18px;margin:12px 0;box-shadow:0 2px 14px #00000012}h1{font-size:1.5rem;margin:.2rem 0 1rem}h2{font-size:1.1rem}button,.btn,select,input{font:inherit}input,select{width:100%;box-sizing:border-box;padding:12px;border:1px solid #ccd3db;border-radius:10px;margin:6px 0 12px}button,.btn{padding:12px 16px;border:0;border-radius:10px;background:#1f6feb;color:white;cursor:pointer;text-decoration:none;display:inline-block}button.secondary,.btn.secondary{background:#566573}.danger{color:#b42318}.ok{color:#067647}.muted{color:#667085;font-size:.9rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.kw{background:#eef4ff;color:#1849a9}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#f2f4f7;margin:3px;font-size:.88rem}table{width:100%;border-collapse:collapse;font-size:.88rem}th,td{padding:8px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}.scroll{overflow-x:auto}#preview{max-width:100%;border-radius:12px;display:none;margin-top:8px}
</style></head><body><div class="wrap">{{ body|safe }}</div></body></html>
'''

def render_page(title, body):
    return render_template_string(BASE_HTML, title=title, body=body)

def require_role(role):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for("login", role=role))
            return fn(*args, **kwargs)
        return inner
    return deco

@app.route("/")
def home():
    return render_page("Clinic", '<div class="card"><h1>Clinic ICD-10 & รง.501</h1><p>เลือกหน้าที่ต้องการ</p><a class="btn" href="/doctor">แพทย์</a> <a class="btn secondary" href="/dashboard">สาธารณสุข / Dashboard</a></div>')

@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):
    if role not in ("doctor", "public_health"):
        return "Invalid role", 400
    expected = DOCTOR_PIN if role == "doctor" else PUBLIC_HEALTH_PIN
    if request.method == "POST":
        if expected and request.form.get("pin", "") == expected:
            session["role"] = role
            return redirect("/doctor" if role == "doctor" else "/dashboard")
        return render_page("Login", '<div class="card"><h1>PIN ไม่ถูกต้อง</h1><a class="btn" href="">ลองใหม่</a></div>')
    label = "แพทย์" if role == "doctor" else "สาธารณสุข"
    return render_page("Login", f'<div class="card"><h1>เข้าสู่ระบบ: {label}</h1><form method="post"><input name="pin" type="password" inputmode="numeric" placeholder="PIN" required><button type="submit">เข้าสู่ระบบ</button></form></div>')

@app.route("/logout")
def logout():
    session.clear(); return redirect("/")

@app.route("/doctor")
@require_role("doctor")
def doctor():
    buttons = "".join([f'<button class="kw" type="button" onclick="chooseKeyword(\'{k}\')">{k}</button>' for k in DIAGNOSIS_MAP])
    body = '''
<div class="card"><h1>แพทย์: บันทึก Diagnosis → ICD-10</h1><div class="muted">ภาพที่ถ่ายใช้เพื่ออ่าน HN ชั่วคราวและไม่บันทึกลง GitHub</div></div>
<div class="card"><h2>1) ถ่าย HN จากเวชระเบียน</h2><input id="camera" type="file" accept="image/*" capture="environment"><img id="preview"><button type="button" onclick="readID()">AI อ่าน HN</button><div id="ocrStatus" class="muted"></div><label>HN — กรุณาตรวจและ Confirm</label><input id="hn" autocapitalize="characters" maxlength="32" placeholder="เช่น 00123456 หรือ 12/3456"></div>
<div class="card"><h2>2) เลือก Keyword</h2><div class="grid">__BUTTONS__</div><p>เลือกแล้ว: <strong id="chosenKeyword">-</strong></p></div>
<div class="card"><h2>3) เลือก ICD-10 ที่จำเพาะขึ้น</h2><select id="icdSelect"><option value="">โปรดเลือก Keyword ก่อน</option></select></div>
<div class="card"><h2>4) Confirm & Save</h2><label><input id="confirm" type="checkbox" style="width:auto"> แพทย์ตรวจสอบเลขบัตรและ ICD-10 แล้ว</label><br><br><button type="button" onclick="saveRecord()">Save</button><p id="saveStatus"></p></div>
<div class="card"><a class="btn secondary" href="/logout">ออกจากระบบ</a></div>
<script>
const MAP = __MAP__;
let selectedKeyword = "";
const camera = document.getElementById("camera");
camera.addEventListener("change",()=>{const f=camera.files[0];if(!f)return;const img=document.getElementById("preview");img.src=URL.createObjectURL(f);img.style.display="block";});
function chooseKeyword(k){selectedKeyword=k;document.getElementById("chosenKeyword").textContent=k;const s=document.getElementById("icdSelect");s.innerHTML='<option value="">-- เลือก ICD-10 --</option>';MAP[k].forEach(x=>{const o=document.createElement("option");o.value=x[0]+"||"+x[1];o.textContent=x[0]+" — "+x[1];s.appendChild(o);});}
async function readID(){const f=camera.files[0],st=document.getElementById("ocrStatus");if(!f){st.textContent="กรุณาถ่ายภาพก่อน";return;}st.textContent="กำลังอ่าน HN...";const fd=new FormData();fd.append("image",f);try{const r=await fetch("/api/read-hn",{method:"POST",body:fd});const j=await r.json();if(!r.ok)throw new Error(j.error||"อ่านไม่สำเร็จ");document.getElementById("hn").value=j.hn||"";st.innerHTML='<span class="ok">อ่านแล้ว กรุณาเทียบกับเวชระเบียนและ Confirm</span>';}catch(e){st.innerHTML='<span class="danger">'+e.message+'</span>';}}
async function saveRecord(){const hn=document.getElementById("hn").value.trim(),val=document.getElementById("icdSelect").value,confirmed=document.getElementById("confirm").checked,out=document.getElementById("saveStatus");if(!hn){out.innerHTML='<span class="danger">กรุณาตรวจสอบ HN</span>';return;}if(!selectedKeyword||!val){out.innerHTML='<span class="danger">กรุณาเลือก Keyword และ ICD-10</span>';return;}if(!confirmed){out.innerHTML='<span class="danger">กรุณา Confirm ก่อน Save</span>';return;}const [icd10,diagnosis]=val.split("||");out.textContent="กำลังบันทึก...";const r=await fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hn:hn,keyword:selectedKeyword,icd10,diagnosis})});const j=await r.json();if(!r.ok){out.innerHTML='<span class="danger">'+(j.error||"Save failed")+'</span>';return;}out.innerHTML='<span class="ok">บันทึกแล้ว '+j.timestamp_bkk+'</span>';document.getElementById("confirm").checked=false;}
</script>
'''.replace("__BUTTONS__", buttons).replace("__MAP__", json.dumps(DIAGNOSIS_MAP, ensure_ascii=False))
    return render_page("Doctor", body)

def openai_read_hn(image_bytes, mime):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {"model": OPENAI_MODEL, "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "Read ONLY the patient HN (Hospital Number) visible in this medical-record image. Return only the HN exactly as printed, preserving leading zeros and any letters, slash, or hyphen. Do not return labels, names, explanations, or other text. If a confident HN is not visible, return UNKNOWN."},
        {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}
    ]}]}
    r = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"AI error {r.status_code}: {r.text[:300]}")
    text = ""
    for item in r.json().get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text"):
                text += c.get("text", "")
    text = text.strip()
    if not text or text.upper() == "UNKNOWN":
        return ""
    # Accept common HN formats while preserving leading zeros.
    # Limit length to reduce accidental capture of unrelated text.
    m = re.search(r"(?i)\b([A-Z0-9][A-Z0-9\-/]{1,31})\b", text)
    return m.group(1) if m else ""

@app.post("/api/read-hn")
@require_role("doctor")
def read_hn():
    f = request.files.get("image")
    if not f: return jsonify(error="ไม่พบภาพ"), 400
    raw = f.read()
    if len(raw) > 8*1024*1024: return jsonify(error="ภาพใหญ่เกิน 8 MB"), 400
    try:
        hn = openai_read_hn(raw, f.mimetype or "image/jpeg")
        if not hn: return jsonify(error="AI อ่าน HN ไม่ชัด กรุณากรอกเอง"), 422
        return jsonify(hn=hn)
    finally:
        raw = b""

def gh_headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}

def gh_url():
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_CSV_PATH}"

def read_csv_from_github():
    if not all([GITHUB_OWNER, GITHUB_REPO, GITHUB_TOKEN]): raise RuntimeError("GitHub environment variables are incomplete")
    r = requests.get(gh_url(), headers=gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=20)
    if r.status_code == 404: return [], None
    if not r.ok: raise RuntimeError(f"GitHub read error {r.status_code}: {r.text[:300]}")
    j = r.json(); content = base64.b64decode(j["content"]).decode("utf-8-sig")
    return (list(csv.DictReader(io.StringIO(content))) if content.strip() else []), j["sha"]

def put_csv_to_github(rows, sha):
    sio = io.StringIO(); w = csv.DictWriter(sio, fieldnames=FIELDS); w.writeheader()
    for row in rows: w.writerow({k: row.get(k, "") for k in FIELDS})
    body = {"message": f"Add clinic diagnosis {datetime.now(BKK).isoformat(timespec='seconds')}", "content": base64.b64encode(sio.getvalue().encode()).decode(), "branch": GITHUB_BRANCH}
    if sha: body["sha"] = sha
    return requests.put(gh_url(), headers=gh_headers(), json=body, timeout=25)

def append_record_with_retry(record, max_attempts=5):
    last = None
    for attempt in range(max_attempts):
        rows, sha = read_csv_from_github(); rows.append(record); r = put_csv_to_github(rows, sha)
        if r.ok: return
        last = r
        if r.status_code in (409, 422): time.sleep(0.35*(attempt+1)); continue
        break
    raise RuntimeError(f"GitHub save error {last.status_code if last else '?'}: {(last.text if last else '')[:300]}")

@app.post("/api/save")
@require_role("doctor")
def save():
    j = request.get_json(force=True); hn = str(j.get("hn", "")).strip(); kw = j.get("keyword", ""); icd10 = j.get("icd10", "").strip(); diagnosis = j.get("diagnosis", "").strip()
    if not hn or len(hn) > 32 or not re.fullmatch(r"[A-Za-z0-9\-/]+", hn): return jsonify(error="HN ไม่ถูกต้อง"), 400
    if kw not in DIAGNOSIS_MAP: return jsonify(error="Keyword ไม่ถูกต้อง"), 400
    if (icd10, diagnosis) not in {(x[0], x[1]) for x in DIAGNOSIS_MAP[kw]}: return jsonify(error="ICD-10 ไม่ตรงกับรายการที่อนุญาต"), 400
    sname = SURVEILLANCE_CODES.get(icd10, "")
    record = {"timestamp_bkk": datetime.now(BKK).isoformat(timespec="seconds"), "hn": hn, "keyword": kw, "icd10": icd10, "diagnosis": diagnosis, "surveillance": "Y" if sname else "N", "surveillance_name": sname}
    try: append_record_with_retry(record)
    except Exception as e: return jsonify(error=str(e)), 500
    return jsonify(ok=True, timestamp_bkk=record["timestamp_bkk"])

def parse_dt(s):
    try: return datetime.fromisoformat(s)
    except Exception: return None

def in_range(dt, mode):
    now = datetime.now(BKK)
    if not dt: return False
    if dt.tzinfo is None: dt = dt.replace(tzinfo=BKK)
    if mode == "day": return dt.date() == now.date()
    if mode == "week":
        start = (now - timedelta(days=now.weekday())).date(); return start <= dt.date() <= start + timedelta(days=6)
    if mode == "month": return dt.year == now.year and dt.month == now.month
    if mode == "year": return dt.year == now.year
    return True

@app.route("/dashboard")
@require_role("public_health")
def dashboard():
    mode = request.args.get("mode", "day")
    if mode not in ("day", "week", "month", "year", "all"): mode = "day"
    try:
        rows, _ = read_csv_from_github(); rows = [r for r in rows if r.get("surveillance") == "Y" and in_range(parse_dt(r.get("timestamp_bkk", "")), mode)]; err = ""
    except Exception as e:
        rows = []; err = str(e)
    counts = {}
    for r in rows:
        key = (r.get("icd10", ""), r.get("surveillance_name", "") or r.get("diagnosis", "")); counts[key] = counts.get(key, 0) + 1
    count_html = "".join([f'<span class="pill"><b>{code}</b> {name}: {n}</span>' for (code, name), n in sorted(counts.items(), key=lambda x: -x[1])]) or '<span class="muted">ยังไม่มีรายการในช่วงที่เลือก</span>'
    trs = ""
    for r in sorted(rows, key=lambda x: x.get("timestamp_bkk", ""), reverse=True):
        hn = r.get("hn", ""); masked = (("x" * max(0, len(hn)-4)) + hn[-4:]) if len(hn) > 4 else hn
        trs += f"<tr><td>{r.get('timestamp_bkk','')}</td><td>{masked}</td><td>{r.get('icd10','')}</td><td>{r.get('surveillance_name','')}</td></tr>"
    labels = [("day","วันนี้"),("week","สัปดาห์นี้"),("month","เดือนนี้"),("year","ปีนี้"),("all","ทั้งหมด")]
    opts = "".join([f'<option value="{x}" {"selected" if x==mode else ""}>{label}</option>' for x, label in labels])
    body = f'<div class="card"><h1>Dashboard รง.501 / โรคเฝ้าระวัง</h1><div class="muted">ต้นแบบนี้ใช้ surveillance mapping ตัวอย่าง ต้องตรวจเทียบกับบัญชี DDC/แนวทางหน่วยงานก่อนใช้งานจริง</div></div><div class="card"><form method="get"><label>ช่วงเวลา</label><select name="mode" onchange="this.form.submit()">{opts}</select></form><h2>รวม {len(rows)} ราย</h2>{count_html}{f"<p class=\"danger\">{err}</p>" if err else ""}</div><div class="card scroll"><table><thead><tr><th>เวลา</th><th>HN (masked)</th><th>ICD-10</th><th>โรคเฝ้าระวัง</th></tr></thead><tbody>{trs}</tbody></table></div><div class="card"><a class="btn secondary" href="/logout">ออกจากระบบ</a></div>'
    return render_page("Dashboard", body)

@app.get("/health")
def health():
    return jsonify(ok=True, time=datetime.now(BKK).isoformat())

if __name__ == "__main__": app.run(debug=True)
