"""
openphish_updater.py — OpenPhish 피드 자동 업데이트
──────────────────────────────────────────────────────────────
서버 시작 1분 후부터 백그라운드에서 자동 실행돼요.
12시간마다 최신 피싱 URL을 가져와서 RAG DB에 추가해요.
──────────────────────────────────────────────────────────────
"""

import requests
import threading
import time
import re
from datetime import datetime
from rag_engine import col_blacklist

FEED_URL        = "https://openphish.com/feed.txt"
UPDATE_INTERVAL = 12 * 60 * 60   # 12시간
DELAY_START     = 60              # 서버 시작 후 1분 뒤 첫 실행
LOG_FILE        = "openphish_update.log"


def log(msg: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def extract_domain(url: str) -> str:
    domain = re.sub(r'^https?://', '', url.strip())
    return domain.split('/')[0]


def make_uid(url: str) -> str:
    return f"op_{abs(hash(url)) % 10000000:07d}"


def already_exists(uid: str) -> bool:
    try:
        result = col_blacklist.get(ids=[uid])
        return len(result['ids']) > 0
    except Exception:
        return False


def fetch_openphish_feed() -> list:
    try:
        resp = requests.get(
            FEED_URL,
            timeout=30,
            headers={'User-Agent': 'CodeShot-Security-Research/1.0'}
        )
        if resp.status_code == 200:
            urls = [line.strip() for line in resp.text.splitlines() if line.strip()]
            log(f"✅ OpenPhish 피드 수신: {len(urls)}개 URL")
            return urls
        else:
            log(f"⚠️ OpenPhish 응답 오류: HTTP {resp.status_code}")
            return []
    except Exception as e:
        log(f"❌ OpenPhish 피드 수신 실패: {e}")
        return []


def update_rag_from_feed():
    log("🔄 OpenPhish 피드 업데이트 시작...")
    urls = fetch_openphish_feed()
    if not urls:
        return

    before = col_blacklist.count()
    success = 0
    skip = 0
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
        batch_metas.append({
            "type": "피싱URL", "target": "불특정",
            "level": "고위험", "source": "OpenPhish 실시간 피드",
            "date": today, "url": url
        })

        if len(batch_ids) >= 100:
            try:
                col_blacklist.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                success += len(batch_ids)
                log(f"  ✅ {success}건 추가됨...")
            except Exception as e:
                log(f"  ⚠️ 배치 오류: {e}")
            batch_ids, batch_docs, batch_metas = [], [], []

    if batch_ids:
        try:
            col_blacklist.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            success += len(batch_ids)
        except Exception as e:
            log(f"  ⚠️ 마지막 배치 오류: {e}")

    after = col_blacklist.count()
    log(f"✅ 완료 | 신규: {success}건 | 스킵: {skip}건 | 총 DB: {after}건")


def run_updater():
    """서버 시작 1분 후 첫 실행, 이후 12시간마다 반복"""
    log(f"⏰ OpenPhish 업데이터 대기 중... ({DELAY_START}초 후 첫 실행)")
    time.sleep(DELAY_START)  # ← 1분 대기 후 시작 (서버 빠르게 켜지게)

    while True:
        try:
            update_rag_from_feed()
        except Exception as e:
            log(f"❌ 업데이트 오류: {e}")

        next_time = datetime.fromtimestamp(
            time.time() + UPDATE_INTERVAL
        ).strftime('%Y-%m-%d %H:%M:%S')
        log(f"⏰ 다음 업데이트: {next_time}")
        time.sleep(UPDATE_INTERVAL)


def start_background_updater():
    """app.py에서 호출 — 백그라운드 스레드로 시작"""
    thread = threading.Thread(
        target=run_updater,
        daemon=True,
        name="OpenPhish-Updater"
    )
    thread.start()
    print(f"✅ OpenPhish 자동 업데이터 등록 (서버 시작 {DELAY_START}초 후 첫 실행)")
