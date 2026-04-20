"""
openphish_updater.py — OpenPhish 피드 자동 업데이트
"""
import requests, threading, time, re, hashlib
from datetime import datetime

FEED_URL        = "https://openphish.com/feed.txt"
UPDATE_INTERVAL = 12 * 60 * 60
DELAY_START     = 60
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
    """col_blacklist를 매번 rag_engine에서 가져옴 (None 방지)"""
    import rag_engine
    return rag_engine.col_blacklist

def extract_domain(url):
    return re.sub(r'^https?://', '', url.strip()).split('/')[0]

def make_uid(url):
    """
    SHA-256 기반 고정 uid 생성.
    Python hash()는 실행마다 값이 달라지므로(hash randomization) 사용 불가.
    SHA-256은 항상 동일한 값을 보장해 중복 체크가 정확히 동작함.
    """
    return "op_" + hashlib.sha256(url.strip().encode()).hexdigest()[:20]

def already_exists(uid):
    try:
        col = get_col()
        if col is None:
            return False
        result = col.get(ids=[uid])
        return len(result['ids']) > 0
    except Exception:
        return False

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

def purge_duplicate_entries():
    """
    기존에 hash() 기반으로 중복 저장된 데이터를 정리하고
    SHA-256 uid 기준으로 재구축.
    최초 1회만 실행 (DB에 op_ 접두사 항목이 많으면 실행).
    """
    col = get_col()
    if col is None:
        return

    total = col.count()
    # 시드 데이터(bl_, case_, pat_ 접두사)는 보존, op_ 접두사만 정리
    log(f"🧹 중복 정리 시작 — 현재 DB: {total}건")
    try:
        # op_ 접두사 항목 전체 조회
        result = col.get(where={"source": {"$eq": "OpenPhish 실시간 피드"}})
        if not result or not result['ids']:
            log("🧹 정리할 OpenPhish 항목 없음")
            return

        ids     = result['ids']
        metas   = result['metadatas']

        # URL 기준 고유 항목만 추출
        seen_urls = {}
        for uid, meta in zip(ids, metas):
            url = meta.get('url', '')
            if not url:
                continue
            correct_uid = make_uid(url)
            if url not in seen_urls:
                seen_urls[url] = {'correct_uid': correct_uid, 'meta': meta, 'old_ids': [uid]}
            else:
                seen_urls[url]['old_ids'].append(uid)

        # 기존 op_ 항목 전체 삭제
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            col.delete(ids=ids[i:i+chunk_size])
        log(f"🧹 기존 {len(ids)}건 삭제 완료")

        # SHA-256 uid로 재삽입
        today = datetime.now().strftime('%Y-%m-%d')
        new_ids, new_docs, new_metas = [], [], []
        for url, info in seen_urls.items():
            domain = extract_domain(url)
            new_ids.append(info['correct_uid'])
            new_docs.append(f"{domain} {url} 피싱사이트 OpenPhish 실시간 탐지 피싱URL")
            meta = info['meta']
            meta['date'] = meta.get('date', today)
            new_metas.append(meta)

            if len(new_ids) >= 100:
                col.add(ids=new_ids, documents=new_docs, metadatas=new_metas)
                new_ids, new_docs, new_metas = [], [], []

        if new_ids:
            col.add(ids=new_ids, documents=new_docs, metadatas=new_metas)

        log(f"✅ 중복 정리 완료 — 정리 후 고유 URL: {len(seen_urls)}건 | 총 DB: {col.count()}건")

    except Exception as e:
        log(f"❌ 중복 정리 오류: {e}")

def update_rag_from_feed():
    col = get_col()
    if col is None:
        log("⚠️ RAG DB 아직 초기화 안 됨, 스킵")
        return

    log("🔄 OpenPhish 피드 업데이트 시작...")
    urls = fetch_openphish_feed()
    if not urls:
        return

    success = skip = 0
    today = datetime.now().strftime('%Y-%m-%d')
    batch_ids, batch_docs, batch_metas = [], [], []

    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        uid = make_uid(url)
        if already_exists(uid):
            skip += 1
            continue

        domain = extract_domain(url)
        batch_ids.append(uid)
        batch_docs.append(f"{domain} {url} 피싱사이트 OpenPhish 실시간 탐지 피싱URL")
        batch_metas.append({"type": "피싱URL", "target": "불특정", "level": "고위험",
                            "source": "OpenPhish 실시간 피드", "date": today, "url": url})

        if len(batch_ids) >= 100:
            try:
                col.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                success += len(batch_ids)
                log(f"  ✅ {success}건 추가됨...")
            except Exception as e:
                log(f"  ⚠️ 배치 오류: {e}")
            batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        try:
            col.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            success += len(batch_ids)
        except Exception as e:
            log(f"  ⚠️ 마지막 배치 오류: {e}")

    after = col.count()
    next_t = datetime.fromtimestamp(time.time() + UPDATE_INTERVAL).strftime('%Y-%m-%d %H:%M:%S')
    log(f"✅ 완료 | 신규: {success}건 | 스킵: {skip}건 | 총 DB: {after}건")
    log(f"⏰ 다음 업데이트: {next_t}")

def run_updater():
    log(f"⏰ OpenPhish 업데이터 대기 중... ({DELAY_START}초 후 첫 실행)")
    time.sleep(DELAY_START)

    # 최초 실행 시 기존 중복 데이터 정리
    try:
        purge_duplicate_entries()
    except Exception as e:
        log(f"⚠️ 중복 정리 중 오류(무시): {e}")

    while True:
        try:
            update_rag_from_feed()
        except Exception as e:
            log(f"❌ 업데이트 오류: {e}")
        time.sleep(UPDATE_INTERVAL)

def start_background_updater():
    thread = threading.Thread(target=run_updater, daemon=True, name="OpenPhish-Updater")
    thread.start()
    print(f"✅ OpenPhish 자동 업데이터 등록 (서버 시작 {DELAY_START}초 후 첫 실행, 중복 정리 포함)")
