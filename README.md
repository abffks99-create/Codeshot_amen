# 코드샷 (CodeShot) 실행 방법

## 1. 패키지 설치
```
pip install -r requirements.txt
```

## 2. MySQL DB 생성
MySQL 접속 후 아래 명령 실행:
```
source create_db.sql
```

## 3. app.py DB 비밀번호 수정
app.py 9번째 줄 `your_password` 부분을 본인 MySQL 비밀번호로 변경:
```python
password='your_password',  ← 여기 수정
```

## 4. 서버 실행
```
python app.py
```
브라우저에서 http://127.0.0.1:5000 접속

---

## 파일 구조
```
codeshot/
├── app.py                  ← Flask 서버 (메인)
├── requirements.txt        ← 설치 목록
├── create_db.sql           ← DB 테이블 생성
├── static/
│   ├── css/
│   │   └── style.css       ← 공통 스타일
│   └── uploads/            ← 업로드 이미지 저장 폴더 (자동 생성)
└── templates/
    ├── login.html          ← UC-101 로그인
    ├── signup.html         ← UC-101 회원가입
    ├── main.html           ← 분석 목록
    ├── upload.html         ← UC-102 이미지 업로드
    ├── result_upload.html  ← UC-102 이미지 분석 결과
    ├── crawl.html          ← UC-103 URL 크롤링 입력
    ├── result_crawl.html   ← UC-103 크롤링 분석 결과
    ├── alerts.html         ← UC-104 알림 목록
    └── mypage.html         ← UC-105 마이페이지
```

## URL 흐름
/ → 로그인 확인 → /login 또는 /main
/login      POST → 로그인 처리
/signup     POST → 회원가입 처리
/main            → 분석 내역 목록
/upload     POST → 이미지 업로드 + 분석
/crawl      POST → URL 크롤링 + 분석
/alerts          → 알림 목록
/alerts/read/<n> → 알림 확인 처리 후 결과로 이동
/mypage     POST → 정보수정 / 비밀번호변경
/withdraw   POST → 회원탈퇴
/logout          → 로그아웃
