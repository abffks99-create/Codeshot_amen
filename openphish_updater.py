"""
openphish_updater.py — OpenPhish 피드 자동 업데이트
7일치 데이터 유지: URL 중복 제거 + 오래된 날짜 하루치 자동 삭제
"""
import requests, threading, time, re, hashlib
from datetime import datetime, timedelta

FEED_URL        = "https://openphish.com/feed.txt"
UPDATE_INTERVAL = 12 * 60 * 60
KEEP_DAYS       = 7   # 유지할 최대 일수
LOG_FILE        = "openphish_update.log"

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def get_col():
    import rag_engine
    return rag_engine.col_blacklist

def extract_domain(url):
    return re.sub(r'^https?://', '', url.strip()).split('/')[0]

def make_uid(url):
    """SHA-256 기반 고정 uid — URL 기준 중복 방지"""
    return "op_" + hashlib.sha256(url.strip().encode()).hexdigest()[:20]

def fetch_openphish_feed():
    try:
        resp = requests.get(FEED_URL, timeout=30,
            headers={'User-Agent': 'CodeShot-Security-Research/1.0'})
        if resp.status_code == 200:
            urls = [l.strip() for l in resp.text.splitlines() if l.strip()]
            log(f"✅ OpenPhish 피드 수신: {len(urls)}개 URL")
            return urls
        log(f"⚠️ OpenPhish 응답 오류: HTTP {resp.status_code}")
        return []
    except Exception as e:
        log(f"❌ OpenPhish 피드 수신 실패: {e}")
        return []

def purge_old_dates(col):
    """
    7일치 초과 시 가장 오래된 날짜 하루치 삭제.
    날짜별 고유 목록을 구해서 KEEP_DAYS 초과분부터 제거.
    """
    try:
        result = col.get(where={"source": {"$eq": "OpenPhish 실시간 피드"}})
        if not result or not result['ids']:
            return

        # 날짜별 id 그룹핑
        date_map = {}
        for uid, meta in zip(result['ids'], result['metadatas']):
            d = meta.get('date', '1970-01-01')
            date_map.setdefault(d, []).append(uid)

        dates_sorted = sorted(date_map.keys())  # 오래된 날짜 순
        excess = len(dates_sorted) - KEEP_DAYS

        if excess <= 0:
            log(f"📅 날짜 보유 현황: {len(dates_sorted)}일치 ({', '.join(dates_sorted)}) — 삭제 없음")
            return

        # 오래된 날짜부터 삭제
        for old_date in dates_sorted[:excess]:
            ids_to_del = date_map[old_date]
            chunk = 500
            for i in range(0, len(ids_to_del), chunk):
                col.delete(ids=ids_to_del[i:i+chunk])
            log(f"🗑 오래된 날짜 삭제: {old_date} — {len(ids_to_del)}건")

        remaining_dates = dates_sorted[excess:]
        log(f"📅 유지 중인 날짜: {', '.join(remaining_dates)}")

    except Exception as e:
        log(f"⚠️ 날짜 정리 중 오류(무시): {e}")

def update_rag_from_feed():
    col = get_col()
    if col is None:
        log("⚠️ RAG DB 아직 초기화 안 됨, 스킵")
        return

    log("🔄 OpenPhish 피드 업데이트 시작...")
    urls = fetch_openphish_feed()
    if not urls:
        return

    today = datetime.now().strftime('%Y-%m-%d')

    # ── 기존 op_ 항목 전체 조회 ───────────────────────────────
    try:
        existing = col.get(where={"source": {"$eq": "OpenPhish 실시간 피드"}})
        existing_uid_set = set(existing['ids'])
        # uid → 날짜 매핑 (날짜 업데이트용)
        uid_to_date = {uid: meta.get('date','') for uid, meta in zip(existing['ids'], existing['metadatas'])}
    except Exception:
        existing_uid_set = set()
        uid_to_date = {}

    # ── 신규/업데이트 처리 ────────────────────────────────────
    add_ids, add_docs, add_metas = [], [], []
    update_ids, update_docs, update_metas = [], [], []
    skip = 0

    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        uid = make_uid(url)
        domain = extract_domain(url)
        doc = f"{domain} {url} 피싱사이트 OpenPhish 실시간 탐지 피싱URL"
        meta = {"type": "피싱URL", "target": "불특정", "level": "고위험",
                "source": "OpenPhish 실시간 피드", "date": today, "url": url}

        if uid in existing_uid_set:
            # 이미 있는 URL — 날짜가 오늘이 아니면 오늘 날짜로 업데이트
            if uid_to_date.get(uid) != today:
                update_ids.append(uid)
                update_docs.append(doc)
                update_metas.append(meta)
            else:
                skip += 1
        else:
            # 새 URL
            add_ids.append(uid)
            add_docs.append(doc)
            add_metas.append(meta)

    # 날짜 업데이트 (삭제 후 재삽입)
    updated = 0
    if update_ids:
        try:
            chunk = 500
            for i in range(0, len(update_ids), chunk):
                col.delete(ids=update_ids[i:i+chunk])
            for i in range(0, len(update_ids), 100):
                col.add(ids=update_ids[i:i+100],
                        documents=update_docs[i:i+100],
                        metadatas=update_metas[i:i+100])
            updated = len(update_ids)
            log(f"  🔄 날짜 갱신: {updated}건 → {today}")
        except Exception as e:
            log(f"  ⚠️ 날짜 갱신 오류: {e}")

    # 신규 추가
    added = 0
    for i in range(0, len(add_ids), 100):
        try:
            col.add(ids=add_ids[i:i+100],
                    documents=add_docs[i:i+100],
                    metadatas=add_metas[i:i+100])
            added += len(add_ids[i:i+100])
            log(f"  ✅ {added}건 추가됨...")
        except Exception as e:
            log(f"  ⚠️ 배치 오류: {e}")

    # ── 7일치 초과 날짜 정리 ──────────────────────────────────
    purge_old_dates(col)

    after = col.count()
    next_t = datetime.fromtimestamp(time.time() + UPDATE_INTERVAL).strftime('%Y-%m-%d %H:%M:%S')
    log(f"✅ 완료 | 신규: {added}건 | 날짜갱신: {updated}건 | 스킵: {skip}건 | 총 DB: {after}건")
    log(f"⏰ 다음 업데이트: {next_t}")

def run_updater():
    log("🚀 OpenPhish 업데이터 시작 — 서버 시작 즉시 첫 실행")
    while True:
        try:
            update_rag_from_feed()
        except Exception as e:
            log(f"❌ 업데이트 오류: {e}")
        time.sleep(UPDATE_INTERVAL)

def start_background_updater():
    thread = threading.Thread(target=run_updater, daemon=True, name="OpenPhish-Updater")
    thread.start()
    print(f"✅ OpenPhish 자동 업데이터 등록 (서버 시작 즉시 첫 실행, 이후 {UPDATE_INTERVAL//3600}시간 주기, 7일치 유지)")
