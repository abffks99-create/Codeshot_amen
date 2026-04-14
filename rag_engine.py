"""
rag_engine.py — CodeShot RAG 엔진
──────────────────────────────────────────────────────────────
세 가지 지식 소스를 벡터DB에 저장하고 분석 시 자동 검색:
  1. 최신 피싱 URL 블랙리스트
  2. 과거 피싱 사례집
  3. 금융감독원·KISA 최신 피싱 패턴
──────────────────────────────────────────────────────────────
설치: pip install chromadb sentence-transformers
"""

import chromadb
from chromadb.utils import embedding_functions
import json, os, re
from datetime import datetime

# ─── 전역 변수 (init_rag() 호출 후 사용 가능) ────────────────
_DB_PATH = "./rag_db"
_client = None
_embedding_fn = None
col_blacklist = None
col_cases = None
col_patterns = None


# ════════════════════════════════════════════════════════════
#  1. 피싱 URL 블랙리스트
# ════════════════════════════════════════════════════════════

BLACKLIST_SEED = [
    # 도메인 위장 패턴
    {"id":"bl_001", "text":"kb0nline-secure.com KB국민은행 사칭 숫자0을 알파벳o처럼 위장한 도메인",
     "meta":{"type":"도메인위장","target":"KB국민은행","level":"고위험","source":"KISA"}},
    {"id":"bl_002", "text":"kakaopay-help.net 카카오페이 고객센터 사칭 비공식 도메인",
     "meta":{"type":"도메인위장","target":"카카오페이","level":"고위험","source":"금감원"}},
    {"id":"bl_003", "text":"naver-secure-login.com 네이버 로그인 사칭 피싱 도메인",
     "meta":{"type":"도메인위장","target":"네이버","level":"고위험","source":"KISA"}},
    {"id":"bl_004", "text":"shinhan-bank-verify.xyz 신한은행 본인인증 사칭",
     "meta":{"type":"도메인위장","target":"신한은행","level":"고위험","source":"금감원"}},
    {"id":"bl_005", "text":"hana-bank-update.online 하나은행 앱 업데이트 유도 사칭",
     "meta":{"type":"도메인위장","target":"하나은행","level":"고위험","source":"KISA"}},
    {"id":"bl_006", "text":"coupang-event-winner.com 쿠팡 이벤트 당첨 사칭 개인정보 탈취",
     "meta":{"type":"브랜드사칭","target":"쿠팡","level":"고위험","source":"KISA"}},
    {"id":"bl_007", "text":"delivery-korea-tracking.net 택배 미수령 문자 피싱 사이트",
     "meta":{"type":"택배사칭","target":"택배","level":"고위험","source":"금감원"}},
    {"id":"bl_008", "text":"irs-tax-refund-kr.com 국세청 세금환급 사칭",
     "meta":{"type":"공공기관사칭","target":"국세청","level":"고위험","source":"KISA"}},
]

def init_blacklist():
    """블랙리스트 초기 데이터 삽입 (중복 방지)"""
    existing = col_blacklist.count()
    if existing >= len(BLACKLIST_SEED):
        return
    for item in BLACKLIST_SEED:
        try:
            col_blacklist.add(
                ids=[item["id"]],
                documents=[item["text"]],
                metadatas=[item["meta"]]
            )
        except Exception:
            pass  # 이미 존재하면 스킵

def add_blacklist_url(url: str, target: str, level: str = "고위험", source: str = "사용자신고"):
    """새 블랙리스트 URL 추가 (관리자 또는 자동 수집)"""
    uid = f"bl_{abs(hash(url)) % 100000:05d}"
    text = f"{url} {target} 사칭 피싱 도메인"
    try:
        col_blacklist.add(
            ids=[uid],
            documents=[text],
            metadatas=[{"type":"도메인위장","target":target,"level":level,
                        "source":source,"added_at":datetime.now().isoformat()}]
        )
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════
#  2. 과거 피싱 사례집
# ════════════════════════════════════════════════════════════

CASES_SEED = [
    {"id":"case_001",
     "text":"KB국민은행 사칭 문자. 보안등급 하락 알림 후 가짜 앱 설치 유도. 앱 설치 시 금융정보 탈취 악성코드 실행",
     "meta":{"type":"스미싱","target":"KB국민은행","damage":"금융정보탈취","year":"2024","source":"금감원"}},
    {"id":"case_002",
     "text":"카카오톡 선물하기 미수령 알림 문자. 링크 클릭 시 카카오 계정 로그인 요구 후 계정 탈취",
     "meta":{"type":"계정탈취","target":"카카오톡","damage":"계정탈취","year":"2024","source":"KISA"}},
    {"id":"case_003",
     "text":"검찰청 사칭 전화. 범죄 연루 혐의 주장 후 계좌 안전조치 명목 이체 유도. 평균 피해액 3000만원",
     "meta":{"type":"보이스피싱","target":"검찰청","damage":"금전피해","year":"2024","source":"경찰청"}},
    {"id":"case_004",
     "text":"건강보험공단 환급금 지급 문자. 환급금 수령 위해 계좌번호 및 주민번호 요구",
     "meta":{"type":"공공기관사칭","target":"건강보험공단","damage":"개인정보탈취","year":"2024","source":"금감원"}},
    {"id":"case_005",
     "text":"네이버 계정 해킹 시도 감지 알림 이메일. 비밀번호 재설정 링크 클릭 시 가짜 네이버 로그인 페이지로 이동",
     "meta":{"type":"피싱이메일","target":"네이버","damage":"계정탈취","year":"2023","source":"KISA"}},
    {"id":"case_006",
     "text":"쿠팡 이벤트 1등 당첨 문자. 경품 수령 위해 소액 배송비 결제 요구 후 카드정보 탈취",
     "meta":{"type":"스미싱","target":"쿠팡","damage":"카드정보탈취","year":"2024","source":"금감원"}},
    {"id":"case_007",
     "text":"정부 긴급재난지원금 신청 문자. 링크 클릭 시 주민번호 계좌번호 요구 개인정보 탈취",
     "meta":{"type":"공공기관사칭","target":"정부","damage":"개인정보탈취","year":"2023","source":"KISA"}},
    {"id":"case_008",
     "text":"택배 주소 불명확 재배송 신청 문자. 링크 클릭 시 개인정보 입력 요구 또는 악성앱 설치 유도",
     "meta":{"type":"택배사칭","target":"택배","damage":"개인정보탈취","year":"2024","source":"금감원"}},
    {"id":"case_009",
     "text":"금융감독원 금융소비자보호 문자. 계좌 지급정지 해제 위해 금감원 직원 사칭 원격제어앱 설치 유도",
     "meta":{"type":"기관사칭","target":"금융감독원","damage":"원격제어","year":"2024","source":"금감원"}},
    {"id":"case_010",
     "text":"애플 아이클라우드 저장공간 부족 알림. 결제정보 업데이트 링크 클릭 시 애플 계정 및 카드정보 탈취",
     "meta":{"type":"피싱이메일","target":"애플","damage":"카드정보탈취","year":"2024","source":"KISA"}},
]

def init_cases():
    existing = col_cases.count()
    if existing >= len(CASES_SEED):
        return
    for item in CASES_SEED:
        try:
            col_cases.add(
                ids=[item["id"]],
                documents=[item["text"]],
                metadatas=[item["meta"]]
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════
#  3. 금융감독원·KISA 피싱 패턴
# ════════════════════════════════════════════════════════════

PATTERNS_SEED = [
    {"id":"pat_001",
     "text":"숫자와 알파벳 혼용 도메인 위장: 0(숫자)을 o(알파벳)으로, 1(숫자)을 l(알파벳)로 대체하여 정상 도메인처럼 보이게 함",
     "meta":{"category":"도메인패턴","risk":"고위험","source":"KISA 2024 피싱 동향 보고서"}},
    {"id":"pat_002",
     "text":"긴급·즉시·지금 등 긴박감 유발 문구 사용. 보안등급 하락 계좌정지 등 공포심 자극으로 즉각 행동 유도",
     "meta":{"category":"심리조작패턴","risk":"고위험","source":"금감원 2024 소비자 경보"}},
    {"id":"pat_003",
     "text":"복수 유명 기관 동시 사칭. KB은행과 카카오페이를 동시에 언급하여 신뢰감 높이는 수법",
     "meta":{"category":"신뢰조작패턴","risk":"고위험","source":"금감원 2024 소비자 경보"}},
    {"id":"pat_004",
     "text":"생성 3개월 미만 신규 도메인 피싱 위험도 높음. 정상 금융기관은 수년 이상 운영된 도메인 사용",
     "meta":{"category":"도메인패턴","risk":"중위험","source":"KISA 도메인 위험도 분석"}},
    {"id":"pat_005",
     "text":"HTTPS 사용하지만 공인 인증기관 발급 아닌 자체 서명 인증서 사용. 자물쇠만으로 안전하다고 판단하면 안 됨",
     "meta":{"category":"보안인증패턴","risk":"중위험","source":"KISA 2024 피싱 동향 보고서"}},
    {"id":"pat_006",
     "text":"개인정보 주민번호 계좌번호 비밀번호를 한 페이지에서 한꺼번에 요구하는 수법. 정상 기관은 절대 이렇게 요구하지 않음",
     "meta":{"category":"정보탈취패턴","risk":"고위험","source":"금감원 2024 소비자 경보"}},
    {"id":"pat_007",
     "text":"앱 설치 유도 패턴. 보안앱 업데이트 필수 등 문구로 APK 파일 직접 설치 유도. 공식 앱스토어 외 설치는 악성앱",
     "meta":{"category":"악성앱패턴","risk":"고위험","source":"KISA 모바일 보안 가이드"}},
    {"id":"pat_008",
     "text":"원격제어앱 설치 유도. 팀뷰어 애니데스크 등 원격제어앱 설치 요구는 100% 사기. 금융기관 직원은 원격제어 요구 안 함",
     "meta":{"category":"원격제어패턴","risk":"고위험","source":"경찰청 2024 보이스피싱 수법"}},
    {"id":"pat_009",
     "text":"단축 URL 사용 패턴. bit.ly tinyurl 등 단축 URL로 실제 목적지 숨기는 수법. 단축 URL은 피싱 위험 신호",
     "meta":{"category":"URL패턴","risk":"중위험","source":"KISA 2024 피싱 동향 보고서"}},
    {"id":"pat_010",
     "text":"이벤트 당첨 경품 지급 패턴. 소액 배송비 결제 요구로 카드정보 탈취. 참여한 적 없는 이벤트 당첨은 모두 사기",
     "meta":{"category":"이벤트사기패턴","risk":"고위험","source":"금감원 2024 소비자 경보"}},
    {"id":"pat_011",
     "text":"2024년 최신 피싱 트렌드: AI 생성 가짜 목소리 딥페이크 활용 보이스피싱 급증. 지인 사칭 문자·전화 주의",
     "meta":{"category":"신종패턴","risk":"고위험","source":"KISA 2024 사이버위협 동향"}},
    {"id":"pat_012",
     "text":"QR코드 피싱(큐싱). QR코드 스캔 시 피싱 사이트로 이동. 출처 불명 QR코드 스캔 주의",
     "meta":{"category":"신종패턴","risk":"고위험","source":"금감원 2024 소비자 경보"}},
]

def init_patterns():
    existing = col_patterns.count()
    if existing >= len(PATTERNS_SEED):
        return
    for item in PATTERNS_SEED:
        try:
            col_patterns.add(
                ids=[item["id"]],
                documents=[item["text"]],
                metadatas=[item["meta"]]
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════
#  검색 함수 — 분석 시 자동 호출
# ════════════════════════════════════════════════════════════

def search_rag(query: str, n_each: int = 3) -> dict:
    """
    세 컬렉션에서 동시에 유사 항목 검색
    반환: {blacklist, cases, patterns} 각각의 결과 리스트
    """
    def _search(collection, q, n):
        try:
            res = collection.query(query_texts=[q], n_results=min(n, collection.count()))
            items = []
            for doc, meta, dist in zip(
                res['documents'][0],
                res['metadatas'][0],
                res['distances'][0]
            ):
                # 거리 0.8 이하만 채택 (관련도 높은 것만)
                if dist <= 0.8:
                    items.append({"text": doc, "meta": meta, "score": round(1 - dist, 3)})
            return items
        except Exception:
            return []

    return {
        "blacklist": _search(col_blacklist, query, n_each),
        "cases":     _search(col_cases,     query, n_each),
        "patterns":  _search(col_patterns,  query, n_each),
    }


def build_rag_context(rag_result: dict) -> str:
    """
    검색 결과를 프롬프트에 삽입할 텍스트로 변환
    """
    lines = []

    if rag_result["blacklist"]:
        lines.append("【피싱 URL 블랙리스트 — 유사 사례】")
        for r in rag_result["blacklist"]:
            m = r["meta"]
            lines.append(f"  • [{m.get('target','?')} 사칭] {r['text']} (출처: {m.get('source','?')}, 위험도: {m.get('level','?')})")

    if rag_result["cases"]:
        lines.append("\n【과거 피싱 사례집】")
        for r in rag_result["cases"]:
            m = r["meta"]
            lines.append(f"  • [{m.get('type','?')}] {r['text']} (피해유형: {m.get('damage','?')}, {m.get('year','?')}년, 출처: {m.get('source','?')})")

    if rag_result["patterns"]:
        lines.append("\n【금융감독원·KISA 공식 피싱 패턴】")
        for r in rag_result["patterns"]:
            m = r["meta"]
            lines.append(f"  • [{m.get('category','?')}] {r['text']} (출처: {m.get('source','?')})")

    if not lines:
        return "관련 피싱 사례 없음 (신종 수법일 가능성 고려)"

    return "\n".join(lines)


def check_blacklist_exact(url: str) -> bool:
    """URL이 블랙리스트에 정확히 있는지 빠른 체크"""
    try:
        domain = re.sub(r'^https?://', '', url).split('/')[0]
        results = col_blacklist.query(query_texts=[domain], n_results=1)
        if results['distances'][0] and results['distances'][0][0] < 0.15:
            return True
    except Exception:
        pass
    return False


# ════════════════════════════════════════════════════════════
#  초기화 실행
# ════════════════════════════════════════════════════════════

def init_rag():
    """앱 시작 시 1회 호출 — 모델 로딩 + DB 초기화"""
    global _client, _embedding_fn, col_blacklist, col_cases, col_patterns

    print("🔍 RAG 지식베이스 초기화 중...")

    # ── 임베딩 모델 로딩 (여기서만 실행) ──────────────────────
    print("  📦 한국어 임베딩 모델 로딩 중...")
    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="jhgan/ko-sroberta-multitask"
    )

    # ── ChromaDB 연결 ──────────────────────────────────────────
    _client = chromadb.PersistentClient(path=_DB_PATH)

    # ── 컬렉션 3개 생성 ───────────────────────────────────────
    col_blacklist = _client.get_or_create_collection(
        name="phishing_blacklist",
        embedding_function=_embedding_fn,
        metadata={"description": "피싱 URL 블랙리스트"}
    )
    col_cases = _client.get_or_create_collection(
        name="phishing_cases",
        embedding_function=_embedding_fn,
        metadata={"description": "과거 피싱 사례집"}
    )
    col_patterns = _client.get_or_create_collection(
        name="phishing_patterns",
        embedding_function=_embedding_fn,
        metadata={"description": "금융감독원·KISA 피싱 패턴"}
    )

    # ── 시드 데이터 삽입 ──────────────────────────────────────
    init_blacklist()
    init_cases()
    init_patterns()

    print(f"  ✅ 블랙리스트: {col_blacklist.count()}건")
    print(f"  ✅ 피싱 사례: {col_cases.count()}건")
    print(f"  ✅ 피싱 패턴: {col_patterns.count()}건")
    print("🔍 RAG 준비 완료")
