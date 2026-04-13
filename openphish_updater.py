"""
openphish_updater.py — OpenPhish 피드 자동 업데이트
──────────────────────────────────────────────────────────────
OpenPhish 무료 피드(https://openphish.com/feed.txt)를
12시간마다 자동으로 크롤링해서 RAG DB에 추가해요.

app.py 실행 시 백그라운드에서 자동으로 동작해요.
──────────────────────────────────────────────────────────────
"""

import requests
import threading
import time
import re
import os
from datetime import datetime
from rag_engine import col_blacklist

# ─── 설정 ────────────────────────────────────────────────────
FEED_URL      = "https://openphish.com/feed.txt"
UPDATE_INTERVAL = 12 * 60 * 60   # 12시간 (초 단위)
LOG_FILE      = "openphish_update.log"


def log(msg: str):
    """로그 출력 + 파일 저장"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def extract_domain(url: str) -> str:
    """URL에서 도메인 추출"""
    domain = re.sub(r'^https?://', '', url.strip())
    return domain.split('/')[0]


def make_uid(url: str) -> str:
    """URL 기반 고유 ID 생성"""
    return f"op_{abs(hash(url)) % 10000000:07d}"


def already_exists(uid: str) -> bool:
    """이미 DB에 있는지 확인"""
    try:
        result = col_blacklist.get(ids=[uid])
        return len(result['ids']) > 0
    except Exception:
        return False


def fetch_openphish_feed() -> list:
    """OpenPhish 피드에서 URL 목록 가져오기"""
    try:
        resp = requests.get(
            FEED_URL,
            timeout=30,
            headers={'User-Agent': 'CodeShot-Security-Research/1.0'}
        )
        if resp.status_code == 200:
            urls = [line.strip() for line in resp.text.splitlines() if line.strip()]
            log(f"✅ OpenPhish 피드 수신 완료: {len(urls)}개 URL")
            return urls
        else:
            log(f"⚠️ OpenPhish 응답 오류: HTTP {resp.status_code}")
            return []
    except Exception as e:
        log(f"❌ OpenPhish 피드 수신 실패: {e}")
        return []


def update_rag_from_feed():
    """피드 URL을 RAG DB에 추가"""
    log("🔄 OpenPhish 피드 업데이트 시작...")

    urls = fetch_openphish_feed()
    if not urls:
        log("⚠️ 업데이트할 URL이 없어요.")
        return

    before = col_blacklist.count()
    success = 0
    skip = 0

    # 100개씩 배치로 처리
    batch_ids, batch_docs, batch_metas = [], [], []
    today = datetime.now().strftime('%Y-%m-%d')

    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        uid = make_uid(url)

        if already_exists(uid):
            skip += 1
            continue

        domain = extract_domain(url)
        doc_text = f"{domain} {url} 피싱사이트 OpenPhish 실시간 탐지 피싱URL"

        batch_ids.append(uid)
        batch_docs.append(doc_text)
        batch_metas.append({
            "type": "피싱URL",
            "target": "불특정",
            "level": "고위험",
            "source": "OpenPhish 실시간 피드",
            "date": today,
            "url": url
        })

        # 배치가 100개 차면 저장
        if len(batch_ids) >= 100:
            try:
                col_blacklist.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas
                )
                success += len(batch_ids)
            except Exception as e:
                log(f"⚠️ 배치 저장 오류: {e}")
            batch_ids, batch_docs, batch_metas = [], [], []

    # 남은 배치 저장
    if batch_ids:
        try:
            col_blacklist.add(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas
            )
            success += len(batch_ids)
        except Exception as e:
            log(f"⚠️ 마지막 배치 저장 오류: {e}")

    after = col_blacklist.count()
    log(f"✅ 업데이트 완료 | 신규 추가: {success}건 | 중복 스킵: {skip}건 | 총 DB: {after}건")


def run_updater():
    """백그라운드에서 12시간마다 자동 업데이트"""
    log("🚀 OpenPhish 자동 업데이트 스케줄러 시작")

    while True:
        try:
            update_rag_from_feed()
        except Exception as e:
            log(f"❌ 업데이트 중 예외 발생: {e}")

        next_update = datetime.fromtimestamp(
            time.time() + UPDATE_INTERVAL
        ).strftime('%Y-%m-%d %H:%M:%S')
        log(f"⏰ 다음 업데이트 예정: {next_update}")

        # 12시간 대기
        time.sleep(UPDATE_INTERVAL)


def start_background_updater():
    """
    app.py에서 호출 — 백그라운드 스레드로 자동 업데이트 시작
    서버가 켜져 있는 동안 계속 동작해요.
    """
    thread = threading.Thread(
        target=run_updater,
        daemon=True,   # 메인 프로그램 종료 시 같이 종료
        name="OpenPhish-Updater"
    )
    thread.start()
    log("✅ OpenPhish 자동 업데이트 백그라운드 스레드 시작됨")
