from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from google import genai
import os, json, re
import mysql.connector
import hashlib
import requests as req
from bs4 import BeautifulSoup
from pathlib import Path
import math
from dotenv import load_dotenv
from rag_engine import init_rag, search_rag, build_rag_context, check_blacklist_exact
from openphish_updater import start_background_updater

# ─── 환경변수 로드 (.env 파일) ───────────────────────────────
load_dotenv()

# ─── Gemini API 설정 ────────────────────────────────────────
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'codeshot_fallback_key')
app.jinja_env.filters['from_json'] = json.loads  # 템플릿에서 JSON 파싱용

# ─── RAG 지식베이스 초기화 ──────────────────────────────────
init_rag()

# ─── OpenPhish 자동 업데이트 시작 (12시간마다) ───────────────
start_background_updater()

# ─── DB 연결 ────────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'codeshot_user'),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', 'codeshot_db'),
        charset='utf8mb4'
    )

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def friendly_ai_error(e):
    msg = str(e)
    if '503' in msg or 'UNAVAILABLE' in msg or 'high demand' in msg:
        return "🔴 AI 서버가 현재 너무 바빠요\n\n구글 AI 서버에 접속자가 몰려 일시적으로 응답하지 못하고 있어요. 마치 인기 식당에 손님이 몰려 잠시 후 다시 방문해 주세요 안내판이 붙은 것과 같은 상황이에요.\n\n✅ 해결 방법: 1~2분 후 다시 시도해 주세요. 사용자의 잘못이 아니에요."
    elif '429' in msg or 'RESOURCE_EXHAUSTED' in msg or 'quota' in msg.lower():
        return "🟡 AI 사용량 한도에 도달했어요\n\n오늘 사용할 수 있는 AI 분석 횟수를 모두 사용했어요.\n\n✅ 해결 방법: 잠시 후 다시 시도하거나 관리자에게 문의해 주세요."
    elif '401' in msg or '403' in msg or 'API_KEY_INVALID' in msg or 'API Key not found' in msg:
        return "🔑 AI 연결 키에 문제가 생겼어요\n\n인증 키가 만료되었거나 올바르지 않아요.\n\n✅ 해결 방법: 관리자에게 문의해 주세요."
    elif 'timeout' in msg.lower():
        return "⏱️ 응답 시간이 초과됐어요\n\n네트워크 상태가 불안정하거나 서버가 느린 경우 발생해요.\n\n✅ 해결 방법: 네트워크 연결을 확인하고 다시 시도해 주세요."
    else:
        return "❌ 일시적인 오류가 발생했어요\n\nAI 분석 중 예상치 못한 문제가 생겼어요.\n\n✅ 해결 방법: 잠시 후 다시 시도해 주세요."

def migrate_db():
    try:
        db = get_db(); cur = db.cursor()
        for col in ['cr_url','cr_col1','cr_col2','cr_col3','cr_col4','cr_col5','cr_col6']:
            cur.execute(f"ALTER TABLE tb_crawling MODIFY {col} TEXT")
        db.commit(); cur.close(); db.close()
    except Exception as e:
        print(f"마이그레이션 오류(무시): {e}")

migrate_db()

# ─── 허용 이미지 확장자 ──────────────────────────────────────
ALLOWED_EXTS = {'png','jpg','jpeg','gif','bmp','webp','tiff','tif','heic','heif','svg','ico'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[-1].lower() in ALLOWED_EXTS

# ─── UC-101 : 로그인 / 회원가입 ─────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('main'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        user_id = request.form['id']
        pwd = hash_pw(request.form['pwd'])
        db = get_db(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM tb_user WHERE id=%s AND pwd=%s", (user_id, pwd))
        user = cur.fetchone(); cur.close(); db.close()
        if user:
            session['user_id'] = user['id']; session['user_name'] = user['name']
            return redirect(url_for('main'))
        error = '아이디 또는 비밀번호가 올바르지 않습니다.'
    return render_template('login.html', error=error)

@app.route('/signup', methods=['GET','POST'])
def signup():
    error = None
    if request.method == 'POST':
        uid = request.form['id']; pwd = hash_pw(request.form['pwd'])
        name = request.form['name']; email = request.form['email']; phone = request.form['phone']
        db = get_db(); cur = db.cursor()
        try:
            cur.execute("INSERT INTO tb_user (id,pwd,name,email,phone,role) VALUES (%s,%s,%s,%s,%s,'user')",
                        (uid,pwd,name,email,phone))
            db.commit(); return redirect(url_for('login'))
        except mysql.connector.IntegrityError:
            error = '이미 사용 중인 아이디 또는 이메일+연락처 조합입니다.'
        finally:
            cur.close(); db.close()
    return render_template('signup.html', error=error)

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

# ─── 메인 (페이지네이션 + 통계) ──────────────────────────────
@app.route('/main')
def main():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = 10
    tab = request.args.get('tab', 'all')  # all, crawling, upload

    db = get_db(); cur = db.cursor(dictionary=True)

    if tab == 'crawling':
        query = """
            SELECT c.cr_idx, c.cr_url AS title, c.created_at,
                   d.ciritical_level, d.deep_idx AS analysis_id, 'crawling' AS type, c.cr_idx AS record_id
            FROM tb_crawling c
            LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
            WHERE c.id = %s ORDER BY c.created_at DESC
        """
        cur.execute(query, (session['user_id'],))
    elif tab == 'upload':
        query = """
            SELECT u.upload_idx, u.file_name AS title, u.uploaded_at AS created_at,
                   d.deep_result AS ciritical_level, d.deep_idx AS analysis_id, 'upload' AS type, u.upload_idx AS record_id
            FROM tb_upload u
            LEFT JOIN tb_deep_upload d ON u.upload_idx = d.upload_idx
            WHERE u.id = %s ORDER BY u.uploaded_at DESC
        """
        cur.execute(query, (session['user_id'],))
    else:
        query = """
            SELECT c.cr_idx AS id, c.cr_url AS title, c.created_at,
                   d.ciritical_level, d.deep_idx AS analysis_id, 'crawling' AS type, c.cr_idx AS record_id
            FROM tb_crawling c
            LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
            WHERE c.id = %s
            UNION ALL
            SELECT u.upload_idx AS id, u.file_name AS title, u.uploaded_at AS created_at,
                   d.deep_result AS ciritical_level, d.deep_idx AS analysis_id, 'upload' AS type, u.upload_idx AS record_id
            FROM tb_upload u
            LEFT JOIN tb_deep_upload d ON u.upload_idx = d.upload_idx
            WHERE u.id = %s
            ORDER BY created_at DESC
        """
        cur.execute(query, (session['user_id'], session['user_id']))

    all_records = cur.fetchall()

    # 통계
    cur.execute("SELECT COUNT(*) AS cnt FROM tb_crawling WHERE id=%s", (session['user_id'],))
    url_cnt = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM tb_upload WHERE id=%s", (session['user_id'],))
    img_cnt = cur.fetchone()['cnt']
    total_cnt = url_cnt + img_cnt

    cur.close(); db.close()

    total_pages = max(1, math.ceil(len(all_records) / per_page))
    page = max(1, min(page, total_pages))
    records = all_records[(page-1)*per_page : page*per_page]

    return render_template('main.html', records=records, page=page,
                           total_pages=total_pages, tab=tab,
                           total_cnt=total_cnt, url_cnt=url_cnt, img_cnt=img_cnt)

# ─── 전체 분석 내역 페이지 ──────────────────────────────────
@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    per_page = 10

    db = get_db(); cur = db.cursor(dictionary=True)
    query = """
        SELECT c.cr_idx AS id, c.cr_url AS title, c.created_at,
               d.ciritical_level, d.deep_idx AS analysis_id, 'crawling' AS type, c.cr_idx AS record_id
        FROM tb_crawling c
        LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
        WHERE c.id = %s
        UNION ALL
        SELECT u.upload_idx AS id, u.file_name AS title, u.uploaded_at AS created_at,
               d.deep_result AS ciritical_level, d.deep_idx AS analysis_id, 'upload' AS type, u.upload_idx AS record_id
        FROM tb_upload u
        LEFT JOIN tb_deep_upload d ON u.upload_idx = d.upload_idx
        WHERE u.id = %s
        ORDER BY created_at DESC
    """
    cur.execute(query, (session['user_id'], session['user_id']))
    all_records = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS cnt FROM tb_crawling WHERE id=%s", (session['user_id'],))
    url_cnt = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM tb_upload WHERE id=%s", (session['user_id'],))
    img_cnt = cur.fetchone()['cnt']
    cur.close(); db.close()

    total_pages = max(1, math.ceil(len(all_records) / per_page))
    page = max(1, min(page, total_pages))
    records = all_records[(page-1)*per_page : page*per_page]

    return render_template('history.html', records=records, page=page,
                           total_pages=total_pages, url_cnt=url_cnt, img_cnt=img_cnt)

# ─── 분석 내역 개별 삭제 ────────────────────────────────────
@app.route('/delete/upload/<int:upload_idx>', methods=['POST'])
def delete_upload(upload_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_upload WHERE upload_idx=%s AND id=%s", (upload_idx, session['user_id']))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('main'))

@app.route('/delete/crawl/<int:cr_idx>', methods=['POST'])
def delete_crawl(cr_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_crawling WHERE cr_idx=%s AND id=%s", (cr_idx, session['user_id']))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('main'))

# ─── UC-102 : 이미지 업로드 및 분석 ─────────────────────────
@app.route('/upload', methods=['GET','POST'])
def upload():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            return render_template('upload.html', error='파일을 선택해주세요.')
        if not allowed_file(file.filename):
            return render_template('upload.html', error='지원하지 않는 파일 형식입니다.')
        ext = file.filename.rsplit('.',1)[-1].lower()
        save_dir = os.path.join('static','uploads')
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, file.filename)
        file.save(save_path)
        file_size = f"{os.path.getsize(save_path)} bytes"

        db = get_db(); cur = db.cursor()
        cur.execute("INSERT INTO tb_upload (id,file_name,file_size,file_ext) VALUES (%s,%s,%s,%s)",
                    (session['user_id'], file.filename, file_size, ext))
        db.commit(); upload_idx = cur.lastrowid

        try:
            # ── RAG: 이미지 파일명 기반 유사 사례 검색 ──────────
            rag_result = search_rag(f"이미지 피싱 스크린샷 {file.filename}", n_each=2)
            rag_context = build_rag_context(rag_result)

            uploaded_file = client.files.upload(file=Path(save_path))
            image_prompt = f"""당신은 대한민국 최고의 피싱 이미지 분석 전문가입니다.
금융감독원과 KISA의 최신 피싱 패턴 데이터베이스를 보유하고 있습니다.

[RAG 참조 데이터]
{rag_context}

[분석 지침]
첨부된 이미지를 다음 5가지 항목으로 정밀 분석하라:

1. 도메인/브랜드 위장 (Brand Spoofing)
   - 공식 로고·브랜드를 모방했는가?
   - 철자 오류나 유사 도메인이 보이는가?

2. 사칭 대상 식별 (Identity Theft)
   - 어떤 기관/브랜드를 사칭하는가?
   - 여러 기관을 동시에 언급하여 신뢰를 유도하는가?

3. 위험 패턴 매칭 (Pattern Matching)
   - KISA/금감원이 경고한 스미싱·피싱 패턴과 일치하는가?
   - RAG 데이터의 사례와 유사한가?

4. 기술적 위협 요소 (Technical Threat)
   - 악성 URL, QR코드, 앱 설치 유도 문구가 있는가?
   - 개인정보 입력을 요구하는 화면인가?

5. 심리 조작 요소 (Psychological Tactic)
   - '긴급', '당첨', '마감' 등 조급함을 유발하는 문구가 있는가?
   - 클릭을 강요하는 버튼이나 문구가 있는가?

[출력 형식 - 반드시 아래 JSON만 출력, 다른 텍스트 절대 금지]
{{
  "level": "고위험 또는 중위험 또는 저위험 또는 안전",
  "items": [
    {{
      "title": "브랜드 위장",
      "status": "danger 또는 warning 또는 safe",
      "desc": "발견된 구체적 증거와 수법 설명 (2~3문장)"
    }},
    {{
      "title": "사칭 대상",
      "status": "danger 또는 warning 또는 safe",
      "desc": "발견된 구체적 증거와 수법 설명 (2~3문장)"
    }},
    {{
      "title": "위험 패턴",
      "status": "danger 또는 warning 또는 safe",
      "desc": "RAG 데이터 매칭 결과 및 근거 (2~3문장)"
    }},
    {{
      "title": "기술적 위협",
      "status": "danger 또는 warning 또는 safe",
      "desc": "악성 URL, 앱 설치 유도, 개인정보 탈취 시도 여부 (2~3문장)"
    }},
    {{
      "title": "심리 조작",
      "status": "danger 또는 warning 또는 safe",
      "desc": "심리적 조작 요소 발견 여부 (2~3문장)"
    }}
  ],
  "summary": "종합 의견 및 사용자 행동 지침 (3~4문장, AI 한계 명시 포함)"
}}"""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[image_prompt, uploaded_file]
            )
            raw = response.text.strip()
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
            try:
                parsed = json.loads(raw)
                result = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                result = raw
            deep_model = 'gemini-2.5-flash (RAG 강화)'
        except Exception as e:
            result = friendly_ai_error(e); deep_model = 'Gemini (Error)'

        cur.execute("INSERT INTO tb_deep_upload (upload_idx,deep_model,deep_result) VALUES (%s,%s,%s)",
                    (upload_idx, deep_model, result))
        db.commit(); deep_idx = cur.lastrowid

        cur.execute("INSERT INTO tb_alert (id,alert_type,idx_no,deep_idx,alert_msg,sended_at) VALUES (%s,'업로드',%s,%s,%s,NOW())",
                    (session['user_id'], upload_idx, deep_idx, f'이미지 분석 완료: {file.filename}'))
        db.commit(); cur.close(); db.close()
        return redirect(f'/result/upload/{deep_idx}')
    return render_template('upload.html', error=None)

# ─── UC-103 : URL 크롤링 및 분석 ─────────────────────────────
@app.route('/crawl', methods=['GET','POST'])
def crawl():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        url = request.form.get('url','').strip()
        if url and not url.startswith(('http://','https://')):
            url = 'https://' + url
        url = url[:500]
        if not url or '.' not in url:
            return render_template('crawl.html', error='올바른 URL을 입력해주세요.')

        try:
            resp = req.get(url, timeout=5, headers={'User-Agent':'Mozilla/5.0'})
            soup = BeautifulSoup(resp.text, 'html.parser')
            cols = [
                (soup.title.string if soup.title else '제목 없음')[:200],
                ' '.join([p.get_text()[:200] for p in soup.find_all('p')[:2]])[:500],
                ' '.join([a.get('href','') for a in soup.find_all('a')[:5]])[:500],
                ' '.join([img.get('src','') for img in soup.find_all('img')[:3]])[:500],
                ' '.join([m.get('content','') for m in soup.find_all('meta')[:3]])[:500],
                resp.url[:500]
            ]
        except Exception as e:
            cols = ['크롤링 실패'] * 6

        db = get_db(); cur = db.cursor()
        cur.execute("INSERT INTO tb_crawling (id,cr_url,cr_col1,cr_col2,cr_col3,cr_col4,cr_col5,cr_col6) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (session['user_id'], url, *cols))
        db.commit(); cr_idx = cur.lastrowid

        cur.execute("INSERT INTO tb_deep_crawling (crawling_idx,fishing_phrase,check_url,total_analysis,ciritical_level) VALUES (%s,%s,%s,%s,%s)",
                    (cr_idx,'분석 대기 중','안전도 확인 중','AI 분석 중...','분석중'))
        db.commit(); deep_idx = cur.lastrowid

        # ── RAG: 블랙리스트 즉시 체크 ──────────────────────────
        is_blacklisted = check_blacklist_exact(url)

        # ── RAG: 유사 사례·패턴 검색 ───────────────────────────
        rag_query = f"{url} {cols[0]} {cols[1]}"
        rag_result = search_rag(rag_query, n_each=3)
        rag_context = build_rag_context(rag_result)

        # ── RAG 강화 프롬프트 ───────────────────────────────────
        blacklist_warning = (
            "⚠️ 주의: 이 URL은 피싱 블랙리스트와 매우 유사한 도메인 패턴을 가지고 있습니다. "
            "고위험으로 분류할 가능성이 높습니다.\n\n"
            if is_blacklisted else ""
        )

        analysis_prompt = f"""당신은 대한민국 최고의 피싱 탐지 전문가입니다.
금융감독원과 KISA의 최신 피싱 패턴 데이터베이스를 보유하고 있습니다.

{blacklist_warning}
[RAG 참조 데이터]
{rag_context}

[분석 대상]
URL: {url}
페이지 제목: {cols[0]}
본문: {cols[1]}
링크: {cols[2]}
메타: {cols[4]}
최종 URL: {cols[5]}

[분석 지침]
다음 5가지 항목을 순서대로 정밀 분석하라:

1. 도메인 정밀 검사 (Domain Integrity)
   - 공식 주소와 유사한 철자(Typosquatting)를 사용했는가?
   - 숫자 '0'과 알파벳 'o'를 섞는 등의 기만 기법이 있는가?

2. 사칭 대상 식별 (Identity Theft)
   - 특정 금융기관(KB, 카카오페이 등)을 사칭하는가?
   - 신뢰를 주기 위해 여러 기관을 동시에 언급하는가?

3. 위험 패턴 매칭 (Pattern Matching)
   - KISA/금감원이 경고한 최신 스미싱 문구와 일치하는가?
   - RAG 데이터의 블랙리스트·사례집·패턴과 일치하는가?

4. 인프라 신뢰도 (Technical Check)
   - 생성된 지 며칠 되지 않은 신규 도메인인가?
   - HTTPS를 사용하더라도 인증서가 신뢰할 수 없는 수준인가?

5. 심리 조작 분석 (Psychological Tactic)
   - '긴급', '마감', '확인 요망' 등의 단어로 조급함을 유발하는가?
   - 이벤트 당첨, 택배 미수령 등 클릭을 유도하는가?

[출력 형식 - 반드시 아래 JSON만 출력, 다른 텍스트 절대 금지]
{{
  "level": "고위험 또는 중위험 또는 저위험 또는 안전",
  "items": [
    {{
      "title": "도메인 위장",
      "status": "danger 또는 warning 또는 safe",
      "desc": "발견된 구체적 증거와 수법 설명 (2~3문장)"
    }},
    {{
      "title": "사칭 대상",
      "status": "danger 또는 warning 또는 safe",
      "desc": "발견된 구체적 증거와 수법 설명 (2~3문장)"
    }},
    {{
      "title": "위험 패턴",
      "status": "danger 또는 warning 또는 safe",
      "desc": "RAG 데이터 매칭 결과 및 근거 (2~3문장)"
    }},
    {{
      "title": "인프라 신뢰도",
      "status": "danger 또는 warning 또는 safe",
      "desc": "도메인 신뢰도, HTTPS 인증서 등 기술적 분석 (2~3문장)"
    }},
    {{
      "title": "심리 조작",
      "status": "danger 또는 warning 또는 safe",
      "desc": "심리적 조작 요소 발견 여부 (2~3문장)"
    }}
  ],
  "summary": "종합 의견 및 사용자 행동 지침 (3~4문장, AI 한계 명시 포함)"
}}"""

        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=analysis_prompt)
            raw = response.text.strip()
            # JSON 마크다운 제거
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
            # JSON 파싱 시도
            try:
                parsed = json.loads(raw)
                ai_result = json.dumps(parsed, ensure_ascii=False)
                level_str = parsed.get('level', '')
            except Exception:
                ai_result = raw
                level_str = raw

            if '고위험' in level_str: critical_level = '고위험'
            elif '중위험' in level_str: critical_level = '중위험'
            elif '저위험' in level_str: critical_level = '저위험'
            else: critical_level = '안전'
            if is_blacklisted: critical_level = '고위험'
        except Exception as e:
            ai_result = friendly_ai_error(e); critical_level = '오류'

        cur.execute("UPDATE tb_deep_crawling SET fishing_phrase=%s,check_url=%s,total_analysis=%s,ciritical_level=%s WHERE deep_idx=%s",
                    (ai_result[:500],'확인됨',ai_result,critical_level,deep_idx))
        db.commit()
        cur.execute("INSERT INTO tb_alert (id,alert_type,idx_no,deep_idx,alert_msg,sended_at) VALUES (%s,'크롤링',%s,%s,%s,NOW())",
                    (session['user_id'], cr_idx, deep_idx, f'크롤링 분석 완료: {url}'))
        db.commit(); cur.close(); db.close()
        return redirect(url_for('result_crawl', deep_idx=deep_idx))
    return render_template('crawl.html', error=None)

# ─── 분석 결과 페이지 ────────────────────────────────────────
@app.route('/result/upload/<int:deep_idx>')
def result_upload(deep_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""SELECT d.*, u.file_name, u.file_size, u.file_ext, u.uploaded_at
        FROM tb_deep_upload d JOIN tb_upload u ON d.upload_idx=u.upload_idx
        WHERE d.deep_idx=%s AND u.id=%s""", (deep_idx, session['user_id']))
    result = cur.fetchone(); cur.close(); db.close()
    if not result: return redirect(url_for('main'))
    return render_template('result_upload.html', result=result)

@app.route('/result/crawl/<int:deep_idx>')
def result_crawl(deep_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""SELECT d.*, c.cr_url, c.created_at
        FROM tb_deep_crawling d JOIN tb_crawling c ON d.crawling_idx=c.cr_idx
        WHERE d.deep_idx=%s AND c.id=%s""", (deep_idx, session['user_id']))
    result = cur.fetchone(); cur.close(); db.close()
    if not result: return redirect(url_for('main'))
    return render_template('result_crawl.html', result=result)


# ─── 분석 내역 전체 삭제 ────────────────────────────────────
@app.route('/delete/all_records', methods=['POST'])
def delete_all_records():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_crawling WHERE id=%s", (session['user_id'],))
    cur.execute("DELETE FROM tb_upload WHERE id=%s", (session['user_id'],))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('main'))

# ─── UC-104 : 알림 ───────────────────────────────────────────
@app.route('/alerts')
def alerts():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM tb_alert WHERE id=%s ORDER BY sended_at DESC", (session['user_id'],))
    alert_list = cur.fetchall(); cur.close(); db.close()
    return render_template('alerts.html', alerts=alert_list)

@app.route('/alerts/read/<int:alert_idx>')
def read_alert(alert_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("UPDATE tb_alert SET received_yn='Y',received_at=NOW() WHERE alert_idx=%s AND id=%s",
                (alert_idx, session['user_id']))
    db.commit()
    cur.execute("SELECT * FROM tb_alert WHERE alert_idx=%s", (alert_idx,))
    alert = cur.fetchone(); cur.close(); db.close()
    if alert['alert_type'] == '업로드':
        return redirect(url_for('result_upload', deep_idx=alert['deep_idx']))
    return redirect(url_for('result_crawl', deep_idx=alert['deep_idx']))

@app.route('/alerts/read_all', methods=['POST'])
def read_all_alerts():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("UPDATE tb_alert SET received_yn='Y', received_at=NOW() WHERE id=%s AND received_yn='N'",
                (session['user_id'],))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('alerts'))

@app.route('/alerts/delete/<int:alert_idx>', methods=['POST'])
def delete_alert(alert_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_alert WHERE alert_idx=%s AND id=%s", (alert_idx, session['user_id']))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('alerts'))

@app.route('/alerts/delete_all', methods=['POST'])
def delete_all_alerts():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_alert WHERE id=%s", (session['user_id'],))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('alerts'))

# ─── UC-105 : 마이페이지 ─────────────────────────────────────
@app.route('/mypage', methods=['GET','POST'])
def mypage():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    error = None; success = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_info':
            name = request.form['name']; email = request.form['email']; phone = request.form['phone']
            try:
                cur.execute("UPDATE tb_user SET name=%s,email=%s,phone=%s WHERE id=%s",
                            (name,email,phone,session['user_id']))
                db.commit(); session['user_name'] = name; success = '정보가 수정되었습니다.'
            except mysql.connector.IntegrityError:
                error = '이미 사용 중인 이메일 또는 연락처입니다.'
        elif action == 'change_pw':
            old_pw = hash_pw(request.form['old_pw']); new_pw = hash_pw(request.form['new_pw'])
            cur.execute("SELECT pwd FROM tb_user WHERE id=%s", (session['user_id'],))
            user = cur.fetchone()
            if user['pwd'] != old_pw: error = '현재 비밀번호가 올바르지 않습니다.'
            else:
                cur.execute("UPDATE tb_user SET pwd=%s WHERE id=%s", (new_pw, session['user_id']))
                db.commit(); success = '비밀번호가 변경되었습니다.'
    cur.execute("SELECT * FROM tb_user WHERE id=%s", (session['user_id'],))
    user = cur.fetchone(); cur.close(); db.close()
    return render_template('mypage.html', user=user, error=error, success=success)

@app.route('/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_user WHERE id=%s", (session['user_id'],))
    db.commit(); cur.close(); db.close()
    session.clear(); return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
