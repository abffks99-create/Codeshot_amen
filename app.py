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
try:
    from openphish_updater import start_background_updater
    _has_updater = True
except Exception as e:
    print(f"⚠️ OpenPhish 업데이터 로딩 실패(무시): {e}")
    _has_updater = False

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

# ─── RAG 지식베이스 초기화 (reloader 중복 실행 방지) ──────────
# debug=True 시 Flask가 프로세스를 2번 실행 → 모델 로딩도 2번 발생
# WERKZEUG_RUN_MAIN 이 설정된 자식 프로세스에서만 실행
import os as _os
if _os.environ.get('WERKZEUG_RUN_MAIN') or not app.debug:
    init_rag()
    if _has_updater:
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
        name = request.form['name']; email = request.form['email']
        _raw_phone = re.sub(r'[^0-9]', '', request.form['phone'])
        if len(_raw_phone) == 11:
            phone = f"{_raw_phone[:3]}-{_raw_phone[3:7]}-{_raw_phone[7:]}"
        elif len(_raw_phone) == 10:
            phone = f"{_raw_phone[:3]}-{_raw_phone[3:6]}-{_raw_phone[6:]}"
        else:
            phone = request.form['phone']
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

    # 사용자 통계
    cur.execute("SELECT COUNT(*) AS cnt FROM tb_crawling WHERE id=%s", (session['user_id'],))
    url_cnt = cur.fetchone()['cnt']
    cur.execute("SELECT COUNT(*) AS cnt FROM tb_upload WHERE id=%s", (session['user_id'],))
    img_cnt = cur.fetchone()['cnt']
    total_cnt = url_cnt + img_cnt

    # ── OpenPhish 위협 현황 ──────────────────────────────────
    import rag_engine as _rag
    import re as _re
    from datetime import date as _date
    col = _rag.col_blacklist
    today_str = _date.today().strftime('%Y-%m-%d')

    # ChromaDB 전체 누적 건수 (고유 URL 기준)
    threat_total_count = col.count() if col else 0

    # 오늘 탐지 건수: 오늘 날짜로 저장된 것 우선, 없으면 전체 누적으로 대체
    try:
        today_res = col.get(where={'date': {'$eq': today_str}}) if col else None
        threat_today_count = len(today_res['ids']) if today_res and today_res['ids'] else 0
    except Exception:
        threat_today_count = 0
    if threat_today_count == 0:
        threat_today_count = threat_total_count

    # 금융기관 사칭 건수: 샘플 URL에서 금융 키워드 포함 고유 도메인 집계
    try:
        sample_res = col.get(limit=min(1000, col.count()), include=['metadatas']) if col else None
        if sample_res and sample_res.get('metadatas'):
            finance_kw = ['bank', 'kb', 'shinhan', 'hana', 'woori', 'nh', 'toss',
                          'pay', 'card', 'finance', 'invest', 'loan', 'credit']
            seen_f = set()
            for meta in sample_res['metadatas']:
                d = _re.sub(r'^https?://', '', meta.get('url', '').lower()).split('/')[0]
                if d and d not in seen_f and any(k in d for k in finance_kw):
                    seen_f.add(d)
            threat_finance_count = len(seen_f)
        else:
            threat_finance_count = 0
    except Exception:
        threat_finance_count = 0

    # 최근 위협 URL: col.get()으로 전체에서 직접 샘플링 후 중복 도메인 제거해 6개 추출
    recent_threats = []
    try:
        if col and col.count() > 0:
            # query() 대신 get()으로 대량 샘플링 — 중복 DB에서도 다양한 도메인 확보
            fetch_n = min(500, col.count())
            res = col.get(
                limit=fetch_n,
                include=['documents', 'metadatas']
            )
            docs  = res.get('documents', [])
            metas = res.get('metadatas', [])
            seen_domains = set()
            for doc, meta in zip(docs, metas):
                if len(recent_threats) >= 6:
                    break
                raw_url = meta.get('url', '')
                if not raw_url:
                    url_match = _re.search(r'https?://\S+', doc)
                    raw_url = url_match.group(0) if url_match else doc.split()[0]
                domain = _re.sub(r'^https?://', '', raw_url).split('/')[0].lower()
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                level  = meta.get('level', '고위험')
                target = meta.get('target', '')
                if not target or target == '불특정':
                    if any(k in domain for k in ['kakao', 'naver', 'google', 'daum']):
                        target = '포털·SNS 사칭'
                    elif any(k in domain for k in ['kb', 'shinhan', 'hana', 'woori', 'nh', 'bank']):
                        target = '금융기관 사칭'
                    elif any(k in domain for k in ['toss', 'pay', 'card']):
                        target = '핀테크·페이 사칭'
                    elif any(k in domain for k in ['coupang', 'baemin', 'delivery']):
                        target = '쇼핑·배달 사칭'
                    else:
                        target = '피싱 사이트'
                recent_threats.append({
                    'domain': domain,
                    'level': level,
                    'category': target,
                    'time_ago': meta.get('date', today_str),
                })
    except Exception:
        pass

    # 사칭 유형 분포 (고정 비율 — 추후 실제 분류 데이터로 교체 가능)
    threat_categories = [
        {'name': '금융·은행', 'pct': 72, 'color': '#F04438'},
        {'name': '포털·SNS', 'pct': 15, 'color': '#F59E0B'},
        {'name': '쇼핑·배송', 'pct':  9, 'color': '#2E90FA'},
        {'name': '기타',      'pct':  4, 'color': '#98A2B3'},
    ]

    # 자주 조회된 의심 도메인
    # 1순위: tb_crawling 실제 분석 기록 중 고위험·주의 (전체 사용자 기준)
    # 2순위: 없으면 OpenPhish DB에서 고유 도메인 샘플링
    import re as _re2
    cur.execute("""
        SELECT cr_url, COUNT(*) AS cnt,
               MAX(d.ciritical_level) AS level
        FROM tb_crawling c
        LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
        WHERE d.ciritical_level IN ('고위험', '주의')
        GROUP BY cr_url
        ORDER BY cnt DESC, MAX(c.created_at) DESC
        LIMIT 6
    """)
    raw_suspects = cur.fetchall()

    def _make_cat(domain):
        dl = domain.lower()
        if any(k in dl for k in ['kakao', 'naver', 'google', 'daum']):
            return '포털·SNS 사칭 의심'
        elif any(k in dl for k in ['kb', 'shinhan', 'hana', 'woori', 'nh', 'bank', 'finance']):
            return '금융기관 사칭 의심'
        elif any(k in dl for k in ['toss', 'pay', 'card']):
            return '핀테크·페이 사칭 의심'
        elif any(k in dl for k in ['coupang', 'gmarket', 'auction', 'baemin', 'delivery']):
            return '쇼핑·배달 사칭 의심'
        return '피싱 의심'

    suspect_domains = []
    if raw_suspects:
        for r in raw_suspects:
            domain = _re2.sub(r'^https?://', '', (r['cr_url'] or '')).split('/')[0]
            suspect_domains.append({
                'domain': domain,
                'count': r['cnt'],
                'level': r['level'] or '주의',
                'category': _make_cat(domain),
            })
    else:
        # fallback: OpenPhish DB 전체에서 고유 도메인 6개 샘플링
        try:
            if col and col.count() > 0:
                fb_res = col.get(
                    limit=min(500, col.count()),
                    include=['metadatas']
                )
                fb_metas = fb_res.get('metadatas', [])
                seen = set()
                for meta in fb_metas:
                    if len(suspect_domains) >= 6:
                        break
                    raw_url = meta.get('url', '')
                    if not raw_url:
                        continue
                    domain = _re2.sub(r'^https?://', '', raw_url).split('/')[0].lower()
                    if not domain or domain in seen:
                        continue
                    seen.add(domain)
                    suspect_domains.append({
                        'domain': domain,
                        'count': '-',
                        'level': meta.get('level', '고위험'),
                        'category': _make_cat(domain),
                    })
        except Exception:
            pass

    cur.close(); db.close()

    total_pages = max(1, math.ceil(len(all_records) / per_page))
    page = max(1, min(page, total_pages))
    records = all_records[(page-1)*per_page : page*per_page]

    return render_template('main.html', records=records, page=page,
                           total_pages=total_pages, tab=tab,
                           total_cnt=total_cnt, url_cnt=url_cnt, img_cnt=img_cnt,
                           recent_threats=recent_threats,
                           threat_today_count=threat_today_count,
                           threat_finance_count=threat_finance_count,
                           threat_total_count=threat_total_count,
                           threat_categories=threat_categories,
                           suspect_domains=suspect_domains)

# ─── 전체 분석 내역 페이지 ──────────────────────────────────
@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    page = request.args.get('page', 1, type=int)
    tab  = request.args.get('tab', 'all')  # all, crawling, upload
    per_page = 10

    db = get_db(); cur = db.cursor(dictionary=True)

    if tab == 'crawling':
        # URL만 필터
        query = """
            SELECT c.cr_idx AS id, c.cr_url AS title, c.created_at,
                   d.ciritical_level, d.deep_idx AS analysis_id, 'crawling' AS type, c.cr_idx AS record_id
            FROM tb_crawling c
            LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
            WHERE c.id = %s ORDER BY c.created_at DESC
        """
        cur.execute(query, (session['user_id'],))
    elif tab == 'upload':
        # 이미지만 필터
        query = """
            SELECT u.upload_idx AS id, u.file_name AS title, u.uploaded_at AS created_at,
                   d.deep_result AS ciritical_level, d.deep_idx AS analysis_id, 'upload' AS type, u.upload_idx AS record_id
            FROM tb_upload u
            LEFT JOIN tb_deep_upload d ON u.upload_idx = d.upload_idx
            WHERE u.id = %s ORDER BY u.uploaded_at DESC
        """
        cur.execute(query, (session['user_id'],))
    else:
        # 전체
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
                           total_pages=total_pages, url_cnt=url_cnt, img_cnt=img_cnt, tab=tab)

# ─── 분석 내역 개별 삭제 ────────────────────────────────────
@app.route('/delete/upload/<int:upload_idx>', methods=['POST'])
def delete_upload(upload_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    tab  = request.form.get('tab', 'all')
    try: page = int(request.form.get('page', 1))
    except: page = 1
    db = get_db(); cur = db.cursor()
    # 관련 알림 삭제 (deep_idx 기준)
    cur.execute("""DELETE a FROM tb_alert a
        INNER JOIN tb_deep_upload du ON a.deep_idx = du.deep_idx
        WHERE du.upload_idx=%s AND a.id=%s""", (upload_idx, session['user_id']))
    # 자식 테이블(tb_deep_upload) 먼저 삭제 후 부모(tb_upload) 삭제
    cur.execute("DELETE FROM tb_deep_upload WHERE upload_idx=%s", (upload_idx,))
    cur.execute("DELETE FROM tb_upload WHERE upload_idx=%s AND id=%s", (upload_idx, session['user_id']))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('history', tab=tab, page=page))

@app.route('/delete/crawl/<int:cr_idx>', methods=['POST'])
def delete_crawl(cr_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    tab  = request.form.get('tab', 'all')
    try: page = int(request.form.get('page', 1))
    except: page = 1
    db = get_db(); cur = db.cursor()
    # 관련 알림 삭제 (deep_idx 기준)
    cur.execute("""DELETE a FROM tb_alert a
        INNER JOIN tb_deep_crawling dc ON a.deep_idx = dc.deep_idx
        WHERE dc.crawling_idx=%s AND a.id=%s""", (cr_idx, session['user_id']))
    # 자식 테이블(tb_deep_crawling) 먼저 삭제 후 부모(tb_crawling) 삭제
    cur.execute("DELETE FROM tb_deep_crawling WHERE crawling_idx=%s", (cr_idx,))
    cur.execute("DELETE FROM tb_crawling WHERE cr_idx=%s AND id=%s", (cr_idx, session['user_id']))
    db.commit(); cur.close(); db.close()
    return redirect(url_for('history', tab=tab, page=page))

# ─── 분석 내역 선택 삭제 ────────────────────────────────────
@app.route('/delete/selected_records', methods=['POST'])
def delete_selected_records():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    tab  = request.form.get('tab', 'all')
    try: page = int(request.form.get('page', 1))
    except: page = 1

    selected = request.form.getlist('selected')  # ["crawl_1", "upload_3", ...]
    if not selected:
        return redirect(url_for('history', tab=tab, page=page))

    db = get_db()
    db.autocommit = False
    cur = db.cursor()
    try:
        for item in selected:
            kind, idx = item.split('_', 1)
            idx = int(idx)
            if kind == 'upload':
                cur.execute("""DELETE a FROM tb_alert a
                    INNER JOIN tb_deep_upload du ON a.deep_idx = du.deep_idx
                    WHERE du.upload_idx=%s AND a.id=%s""", (idx, uid))
                cur.execute("DELETE FROM tb_deep_upload WHERE upload_idx=%s", (idx,))
                cur.execute("DELETE FROM tb_upload WHERE upload_idx=%s AND id=%s", (idx, uid))
            elif kind == 'crawl':
                cur.execute("""DELETE a FROM tb_alert a
                    INNER JOIN tb_deep_crawling dc ON a.deep_idx = dc.deep_idx
                    WHERE dc.crawling_idx=%s AND a.id=%s""", (idx, uid))
                cur.execute("DELETE FROM tb_deep_crawling WHERE crawling_idx=%s", (idx,))
                cur.execute("DELETE FROM tb_crawling WHERE cr_idx=%s AND id=%s", (idx, uid))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[선택삭제 오류] {e}")
    finally:
        cur.close()
        db.close()
    return redirect(url_for('history', tab=tab, page=page))

# ─── 알림 선택 삭제 ─────────────────────────────────────────
@app.route('/alerts/delete_selected', methods=['POST'])
def delete_selected_alerts():
    if 'user_id' not in session: return redirect(url_for('login'))
    selected = request.form.getlist('selected')  # alert_idx 목록
    if selected:
        db = get_db(); cur = db.cursor()
        fmt = ','.join(['%s'] * len(selected))
        cur.execute(f"DELETE FROM tb_alert WHERE alert_idx IN ({fmt}) AND id=%s",
                    (*selected, session['user_id']))
        db.commit(); cur.close(); db.close()
    return redirect(url_for('alerts'))

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
        import uuid as _uuid
        ext = file.filename.rsplit('.',1)[-1].lower()
        # 한글/특수문자 파일명 → UUID로 안전하게 변환
        original_name = file.filename
        safe_filename = f"{_uuid.uuid4().hex}.{ext}"
        save_dir = os.path.join('static','uploads')
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, safe_filename)
        file.save(save_path)
        file_size = f"{os.path.getsize(save_path)} bytes"

        db = get_db(); cur = db.cursor()
        cur.execute("INSERT INTO tb_upload (id,file_name,file_size,file_ext) VALUES (%s,%s,%s,%s)",
                    (session['user_id'], safe_filename, file_size, ext))
        db.commit(); upload_idx = cur.lastrowid

        try:
            # ── RAG: 이미지 파일명 기반 유사 사례 검색 ──────────
            rag_result = search_rag(f"이미지 피싱 스크린샷 {file.filename}", n_each=2)
            rag_context = build_rag_context(rag_result)

            # ── 이미지를 base64로 인코딩하여 직접 전송 (files.upload 불안정 대체) ──
            import base64 as _b64
            with open(save_path, 'rb') as _f:
                img_bytes = _f.read()
            img_b64 = _b64.b64encode(img_bytes).decode('utf-8')
            mime_map = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png',
                        'gif':'image/gif','webp':'image/webp','bmp':'image/bmp',
                        'tiff':'image/tiff','tif':'image/tiff','heic':'image/heic',
                        'heif':'image/heif','svg':'image/svg+xml','ico':'image/x-icon'}
            img_mime = mime_map.get(ext, 'image/jpeg')
            JSON_TEMPLATE = '''
{
  "level": "고위험 또는 주의 또는 안전 중 하나",
  "items": [
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "피싱 의심 요소",
      "desc": "핵심만 1문장으로. 예: '카카오페이 로고 위장 및 긴급 인증 요구 문구 발견.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "사칭 대상 식별",
      "desc": "핵심만 1문장으로. 예: '카카오페이 및 KB국민은행 동시 사칭.' 없으면 '사칭 대상 없음.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "유사 사례 매칭",
      "desc": "핵심만 1문장으로. 예: '금감원 2024 사례집의 카카오 계정 탈취 수법과 일치.' 없으면 '유사 사례 없음.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "위험도 판별",
      "desc": "핵심만 1문장으로. 예: '복수 기관 사칭·긴급 문구·개인정보 요구 — 고위험 3중 패턴 탐지.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나 (naver.com, google.com 등 공식적으로 검증된 사이트는 safe, 위험한 사이트면 danger, 확실히 안전하지 않거나 애매하면 warning)",
      "title": "사용자 행동 권고",
      "desc": "핵심만 1문장으로. 예: '즉시 무시하고 공식 앱에서 직접 확인하세요.'"
    }
  ],
  "summary": "위의 5가지 항목을 종합하여 4~5문장으로 작성하세요. 각 문장은 반드시 줄바꿈(\\n)으로 구분하세요. 이 URL/이미지가 왜 위험한지(또는 안전한지), 어떤 수법이 사용됐는지, 사용자가 지금 당장 해야 할 행동을 쉬운 말로 구체적으로 설명하세요. 예시: \"이 주소는 진짜처럼 보이지만 가짜예요.\\n주소에 글자 하나가 다릅니다.\\n즉시 창을 닫으세요.\""
}
'''
            image_prompt = (
                "당신은 대한민국 최고의 피싱 이미지 분석 전문가입니다.\n"
                "금융감독원과 KISA의 최신 피싱 패턴 데이터베이스를 보유하고 있습니다.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📚 RAG 참조 데이터 (금융감독원·KISA 공식 자료)\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + rag_context +
                "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📋 분석 요청 사항\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "첨부된 이미지를 피싱 탐지 관점에서 분석하고,\n"
                "RAG 참조 데이터를 반드시 참고하여 아래 JSON 형식으로만 답변하세요.\n"
                "JSON 외 다른 텍스트는 절대 출력하지 마세요.\n\n"
                "⚠️ 중요: 각 항목의 desc는 반드시 1문장(50자 이내)으로 핵심만 작성하세요.\n"
                "길게 설명하지 말고 가장 중요한 사실 하나만 담으세요.\n"
                "⚠️ 언어 규칙: 반드시 초등학생도 이해할 수 있는 쉬운 말로만 작성하세요.\n"
                "전문 용어 대신 아래처럼 쉬운 표현을 사용하세요.\n"
                "타이포스쿼팅→주소를 살짝 바꿔 정상 사이트처럼 보이게 하는 수법, "
                "피싱→개인정보를 훔치려는 가짜 사이트, "
                "도메인 위장→인터넷 주소를 비슷하게 흉내낸 것, "
                "악성코드→기기를 망가뜨리거나 정보를 훔치는 나쁜 프로그램, "
                "스미싱→문자 메시지를 이용한 사기, "
                "블랙리스트→위험 목록, 리다이렉트→다른 주소로 자동 이동, "
                "RAG·블랙리스트 등 내부 시스템 용어는 절대 노출하지 마세요.\n"
                "사용자가 즉시 이해하고 행동할 수 있도록 친근하게 쓰세요.\n"
                + JSON_TEMPLATE +
                "\n⚠️ status 값 선택 기준 — 반드시 엄격하게 적용하세요:\n"
                "- safe(안전 초록점): 해당 항목이 100% 확실하게 정상임이 입증될 때만 사용\n"
                "  예) 유사 사례 매칭 → 유사 사례가 전혀 없을 때는 safe가 아닌 warning 사용\n"
                "- warning(주의 노란점): 확인이 필요하거나 애매한 경우, 정보가 부족한 경우\n"
                "- danger(위험 빨간점): 명확한 위협 요소 발견\n"
                "safe는 해당 항목이 완전히 검증된 경우에만 사용하고, 불확실하면 warning을 사용하세요.\n\n"
                "⚠️ level(전체 위험도) 판정 기준:\n"
                "- 안전: 모든 항목이 100% 안전하고 피싱 요소가 전혀 없을 때만 사용\n"
                "- 주의: 의심 요소가 일부 있거나 확실하지 않은 경우 (저위험·중위험 모두 포함)\n"
                "- 고위험: 피싱 패턴이 명확히 발견될 때\n"
                "확실히 안전하다고 보장할 수 없으면 반드시 주의 이상으로 판정하세요.\n\n"
                "답변은 반드시 한국어로 작성하고, JSON만 출력하세요.\n"
            )
            from google.genai import types as _gtypes
            import time as _time
            # 모델 폴백 순서: 2.5-flash → 1.5-flash
            _models = ['gemini-2.5-flash', 'gemini-1.5-flash']
            response = None
            for _model in _models:
                for _attempt in range(2):  # 모델당 2회 재시도
                    try:
                        response = client.models.generate_content(
                            model=_model,
                            contents=[
                                _gtypes.Part.from_text(text=image_prompt),
                                _gtypes.Part.from_bytes(data=img_bytes, mime_type=img_mime),
                            ]
                        )
                        print(f'[UPLOAD] 모델 {_model} 성공 (시도 {_attempt+1})')
                        break
                    except Exception as _e:
                        print(f'[UPLOAD] {_model} 시도 {_attempt+1} 실패: {_e}')
                        if _attempt == 0:
                            _time.sleep(2)  # 재시도 전 2초 대기
                if response:
                    break
            if not response:
                raise Exception('모든 모델 호출 실패')
            raw = response.text.strip()

            # ── JSON 파싱 (강화된 버전) ───────────────────────
            def safe_parse_json(text):
                if '```' in text:
                    text = text.split('```')[1]
                    if text.startswith('json'):
                        text = text[4:]
                text = text.strip()
                start = text.find('{')
                end   = text.rfind('}')
                if start != -1 and end != -1:
                    text = text[start:end+1]
                return json.loads(text)

            try:
                parsed = safe_parse_json(raw)
                result = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                result = raw

            deep_model = 'gemini-2.5-flash (RAG 강화)'
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f'[UPLOAD ERROR] {type(e).__name__}: {e}')
            result = friendly_ai_error(e); deep_model = 'Gemini 2.5 Flash (Error)'

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
            resp = req.get(url, timeout=3, headers={'User-Agent':'Mozilla/5.0'})
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
        print(f'[CRAWL] STEP1: 블랙리스트 체크 시작 url={url}')
        is_blacklisted = check_blacklist_exact(url)
        print(f'[CRAWL] STEP2: 블랙리스트={is_blacklisted}')

        # ── RAG: 유사 사례·패턴 검색 ───────────────────────────
        rag_query = f"{url} {cols[0]} {cols[1]}"
        print('[CRAWL] STEP3: RAG 검색 시작')
        rag_result = search_rag(rag_query, n_each=3)
        rag_context = build_rag_context(rag_result)
        print(f'[CRAWL] STEP4: RAG 컨텍스트 생성 완료, 길이={len(rag_context)}')

        # ── RAG 강화 프롬프트 ───────────────────────────────────
        blacklist_warning = (
            "⚠️ 주의: 이 URL은 피싱 블랙리스트와 매우 유사한 도메인 패턴을 가지고 있습니다. "
            "고위험으로 분류할 가능성이 높습니다.\n\n"
            if is_blacklisted else ""
        )

        JSON_TEMPLATE_CRAWL = '''
{
  "level": "고위험 또는 주의 또는 안전 중 하나",
  "items": [
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "피싱 의심 문구 및 요소",
      "desc": "핵심만 1문장으로. 예: 'KB국민은행 사칭 도메인(kb0nline.com) 및 긴급 보안 문구 발견.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "URL 이상 여부",
      "desc": "핵심만 1문장으로. 예: '숫자 0을 알파벳 o로 위장한 도메인 패턴 — 전형적인 타이포스쿼팅 수법.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "유사 사례 매칭",
      "desc": "핵심만 1문장으로. 예: 'KISA 블랙리스트의 KB국민은행 사칭 도메인 패턴과 일치.' 없으면 '유사 사례 없음.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나",
      "title": "위험도 판별",
      "desc": "핵심만 1문장으로. 예: '도메인 위장·신규 도메인·복수 기관 사칭 3가지 고위험 패턴 동시 탐지.'"
    },
    {
      "status": "danger 또는 warning 또는 safe 중 하나 (naver.com, google.com 등 공식적으로 검증된 사이트는 safe, 위험한 사이트면 danger, 확실히 안전하지 않거나 애매하면 warning)",
      "title": "사용자 행동 권고",
      "desc": "핵심만 1문장으로. 예: '즉시 접속 중단 후 해당 금융사 공식 앱으로 직접 확인하세요.'"
    }
  ],
  "summary": "위의 5가지 항목을 종합하여 4~5문장으로 작성하세요. 각 문장은 반드시 줄바꿈(\\n)으로 구분하세요. 이 URL/이미지가 왜 위험한지(또는 안전한지), 어떤 수법이 사용됐는지, 사용자가 지금 당장 해야 할 행동을 쉬운 말로 구체적으로 설명하세요. 예시: \"이 주소는 진짜처럼 보이지만 가짜예요.\\n주소에 글자 하나가 다릅니다.\\n즉시 창을 닫으세요.\""
}
'''
        analysis_prompt = (
            "당신은 대한민국 최고의 피싱 탐지 전문가입니다.\n"
            "금융감독원과 KISA의 최신 피싱 패턴 데이터베이스를 보유하고 있습니다.\n\n"
            + blacklist_warning +
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📚 RAG 참조 데이터 (금융감독원·KISA 공식 자료)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + rag_context +
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔍 분석 대상 URL 정보\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "URL: " + url + "\n"
            "페이지 제목: " + cols[0] + "\n"
            "본문 내용: " + cols[1] + "\n"
            "링크 목록: " + cols[2] + "\n"
            "이미지 목록: " + cols[3] + "\n"
            "메타 정보: " + cols[4] + "\n"
            "최종 이동 URL: " + cols[5] + "\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 분석 요청 사항\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "위의 RAG 참조 데이터를 반드시 참고하여 분석하고,\n"
            "아래 JSON 형식으로만 답변하세요. JSON 외 다른 텍스트는 절대 출력하지 마세요.\n\n"
            "⚠️ 중요: 각 항목의 desc는 반드시 1문장(50자 이내)으로 핵심만 작성하세요.\n"
            "길게 설명하지 말고 가장 중요한 사실 하나만 담으세요.\n"
            "⚠️ 언어 규칙: 반드시 초등학생도 이해할 수 있는 쉬운 말로만 작성하세요.\n"
            "전문 용어 대신 아래처럼 쉬운 표현을 사용하세요.\n"
            "타이포스쿼팅→주소를 살짝 바꿔 정상 사이트처럼 보이게 하는 수법, "
            "피싱→개인정보를 훔치려는 가짜 사이트, "
            "도메인 위장→인터넷 주소를 비슷하게 흉내낸 것, "
            "악성코드→기기를 망가뜨리거나 정보를 훔치는 나쁜 프로그램, "
            "스미싱→문자 메시지를 이용한 사기, "
            "블랙리스트→위험 목록, 리다이렉트→다른 주소로 자동 이동, "
            "RAG·블랙리스트 등 내부 시스템 용어는 절대 노출하지 마세요.\n"
            "사용자가 즉시 이해하고 행동할 수 있도록 친근하게 쓰세요.\n"
            + JSON_TEMPLATE_CRAWL +
            "\n⚠️ status 값 선택 기준 — 반드시 엄격하게 적용하세요:\n"
            "- safe(안전 초록점): 해당 항목이 100% 확실하게 정상임이 입증될 때만 사용\n"
            "  예) 유사 사례 매칭 → 유사 사례가 전혀 없을 때는 safe가 아닌 warning 사용\n"
            "  예) URL 이상 여부 → URL만 봐서 이상 없어도 내용 확인 전이면 warning 사용\n"
            "- warning(주의 노란점): 확인이 필요하거나 애매한 경우, 정보가 부족한 경우\n"
            "- danger(위험 빨간점): 명확한 위협 요소 발견\n"
            "safe는 해당 항목이 완전히 검증된 경우에만 사용하고, 불확실하면 warning을 사용하세요.\n\n"
            "⚠️ level(전체 위험도) 판정 기준:\n"
            "- 안전: 공식 인증된 기관 사이트이고 모든 항목이 100% 안전할 때만 사용\n"
            "- 주의: 의심 요소가 일부 있거나 확실하지 않은 경우 (저위험·중위험 모두 포함)\n"
            "- 고위험: 피싱 패턴이 명확히 발견될 때\n"
            "확실히 안전하다고 보장할 수 없으면 반드시 주의 이상으로 판정하세요.\n\n"
            "답변은 반드시 한국어로 작성하고, JSON만 출력하세요.\n"
        )

        print('[CRAWL] STEP5: Gemini API 호출 시작')
        try:
            import time as _time2
            _models2 = ['gemini-2.5-flash', 'gemini-1.5-flash']
            response = None
            for _model2 in _models2:
                for _attempt2 in range(2):
                    try:
                        response = client.models.generate_content(model=_model2, contents=analysis_prompt)
                        print(f'[CRAWL] 모델 {_model2} 성공 (시도 {_attempt2+1})')
                        break
                    except Exception as _e2:
                        print(f'[CRAWL] {_model2} 시도 {_attempt2+1} 실패: {_e2}')
                        if _attempt2 == 0:
                            _time2.sleep(2)
                if response:
                    break
            if not response:
                raise Exception('모든 모델 호출 실패')
            raw = response.text.strip()
            print(f'[CRAWL] STEP6: Gemini 응답 수신, 길이={len(raw)}')

            # ── JSON 파싱 (강화된 버전) ───────────────────────
            def safe_parse_json(text):
                # 1) 마크다운 코드블록 제거
                if '```' in text:
                    text = text.split('```')[1]
                    if text.startswith('json'):
                        text = text[4:]
                # 2) 앞뒤 공백 제거
                text = text.strip()
                # 3) JSON 시작/끝 찾기
                start = text.find('{')
                end   = text.rfind('}')
                if start != -1 and end != -1:
                    text = text[start:end+1]
                return json.loads(text)

            try:
                parsed = safe_parse_json(raw)
                ai_result = json.dumps(parsed, ensure_ascii=False)
                level_str = parsed.get('level', '')
            except Exception:
                # JSON 파싱 실패 시 텍스트 그대로 저장
                ai_result = raw
                level_str = raw

            if '고위험' in level_str: critical_level = '고위험'
            elif '주의' in level_str or '저위험' in level_str: critical_level = '주의'
            elif '안전' in level_str: critical_level = '안전'
            else: critical_level = '주의'  # 불명확한 경우 주의로
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
    if not result: return redirect(url_for('alerts'))
    return render_template('result_upload.html', result=result)

@app.route('/result/crawl/<int:deep_idx>')
def result_crawl(deep_idx):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor(dictionary=True)
    cur.execute("""SELECT d.*, c.cr_url, c.created_at
        FROM tb_deep_crawling d JOIN tb_crawling c ON d.crawling_idx=c.cr_idx
        WHERE d.deep_idx=%s AND c.id=%s""", (deep_idx, session['user_id']))
    result = cur.fetchone(); cur.close(); db.close()
    if not result: return redirect(url_for('alerts'))
    return render_template('result_crawl.html', result=result)


# ─── 분석 내역 전체 삭제 ────────────────────────────────────
@app.route('/delete/all_records', methods=['POST'])
def delete_all_records():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    db = get_db()
    db.autocommit = False
    cur = db.cursor()
    try:
        # 자식 테이블 먼저 삭제 (외래키 순서 준수)
        cur.execute("""
            DELETE dc FROM tb_deep_crawling dc
            INNER JOIN tb_crawling c ON dc.crawling_idx = c.cr_idx
            WHERE c.id = %s
        """, (uid,))
        cur.execute("""
            DELETE du FROM tb_deep_upload du
            INNER JOIN tb_upload u ON du.upload_idx = u.upload_idx
            WHERE u.id = %s
        """, (uid,))
        cur.execute("DELETE FROM tb_crawling WHERE id=%s", (uid,))
        cur.execute("DELETE FROM tb_upload WHERE id=%s", (uid,))
        cur.execute("DELETE FROM tb_alert WHERE id=%s", (uid,))
        db.commit()
        print(f"[전체삭제] 완료 - user: {uid}")
    except Exception as e:
        db.rollback()
        print(f"[전체삭제 오류] {e}")
    finally:
        cur.close()
        db.close()
    return redirect('/history')

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
    cur.execute("SELECT * FROM tb_alert WHERE alert_idx=%s AND id=%s", (alert_idx, session['user_id']))
    alert = cur.fetchone(); cur.close(); db.close()
    if not alert: return redirect(url_for('alerts'))
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
            name = request.form['name']; email = request.form['email']
            _raw_phone = re.sub(r'[^0-9]', '', request.form['phone'])
            if len(_raw_phone) == 11:
                phone = f"{_raw_phone[:3]}-{_raw_phone[3:7]}-{_raw_phone[7:]}"
            elif len(_raw_phone) == 10:
                phone = f"{_raw_phone[:3]}-{_raw_phone[3:6]}-{_raw_phone[6:]}"
            else:
                phone = request.form['phone']
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


# ─── API: 자주 조회된 의심 도메인 (탭 전환용) ────────────────

# ─── API: OpenPhish 전체 피싱 URL 목록 ──────────────────────
@app.route('/api/threats_all')
def api_threats_all():
    if 'user_id' not in session: return jsonify([])
    import rag_engine as _rag
    import re as _re4
    col = _rag.col_blacklist
    if not col or col.count() == 0:
        return jsonify([])
    try:
        res = col.get(
            limit=min(col.count(), 10000),
            include=['metadatas']
        )
        metas = res.get('metadatas', [])
        seen = set()
        result = []
        for meta in metas:
            url = meta.get('url', '')
            if not url:
                continue
            domain = _re4.sub(r'^https?://', '', url).split('/')[0].lower()
            if not domain or domain in seen:
                continue
            seen.add(domain)
            dl = domain
            if any(k in dl for k in ['kb','shinhan','hana','woori','nh','bank','finance','loan','credit']):
                cat = '금융기관 사칭'
            elif any(k in dl for k in ['kakao','naver','google','daum','instagram','facebook']):
                cat = '포털·SNS 사칭'
            elif any(k in dl for k in ['toss','pay','card','invest']):
                cat = '핀테크·페이 사칭'
            elif any(k in dl for k in ['coupang','gmarket','auction','baemin','delivery']):
                cat = '쇼핑·배달 사칭'
            else:
                cat = '피싱 사이트'
            result.append({
                'domain': domain,
                'level': meta.get('level', '고위험'),
                'category': cat,
                'date': meta.get('date', ''),
            })
        return jsonify(result)
    except Exception as e:
        print(f'[threats_all] 오류: {e}')
        return jsonify([])

@app.route('/api/suspect_domains')
def api_suspect_domains():
    if 'user_id' not in session: return jsonify([])
    period = request.args.get('period', 'today')
    import re as _re3
    db = get_db(); cur = db.cursor(dictionary=True)
    if period == 'today':
        where = 'DATE(c.created_at) = CURDATE()'
    elif period == 'week':
        where = 'c.created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)'
    else:
        where = '1=1'
    cur.execute(f"""
        SELECT cr_url, COUNT(*) AS cnt,
               MAX(d.ciritical_level) AS level
        FROM tb_crawling c
        LEFT JOIN tb_deep_crawling d ON c.cr_idx = d.crawling_idx
        WHERE ({where}) AND d.ciritical_level IN ('고위험', '주의')
        GROUP BY cr_url
        ORDER BY cnt DESC, MAX(c.created_at) DESC
        LIMIT 5
    """)
    rows = cur.fetchall(); cur.close(); db.close()
    result = []
    for r in rows:
        domain = _re3.sub(r'^https?://', '', (r['cr_url'] or '')).split('/')[0]
        if any(k in domain for k in ['kakao', 'naver', 'google']):
            cat = '포털·SNS 사칭 의심'
        elif any(k in domain for k in ['bank', 'kb', 'shinhan', 'hana', 'woori', 'nh', 'finance']):
            cat = '금융기관 사칭 의심'
        elif any(k in domain for k in ['toss', 'pay', 'card']):
            cat = '핀테크·페이 사칭 의심'
        elif any(k in domain for k in ['coupang', 'gmarket', 'auction', 'baemin']):
            cat = '쇼핑·배달 사칭 의심'
        else:
            cat = '피싱 의심'
        result.append({
            'domain': domain,
            'count': r['cnt'],
            'level': r['level'] or '주의',
            'category': cat,
        })
    return jsonify(result)

@app.route('/withdraw', methods=['POST'])
def withdraw():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cur = db.cursor()
    cur.execute("DELETE FROM tb_user WHERE id=%s", (session['user_id'],))
    db.commit(); cur.close(); db.close()
    session.clear(); return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=False)