# CodeShot 모바일 최적화 수정 내역

## 수정된 파일
- `static/css/style.css` ← 메인 수정 파일
- `templates/mypage.html` ← mypage 개별 수정

---

## 발견된 문제점 & 수정 내용

### 1. iOS 입력창 자동 줌 방지 ✅
**문제:** iOS Safari에서 font-size 16px 미만 input에 포커스 시 화면이 자동으로 확대됨
**수정:** `style.css` 내 모든 input/select/textarea의 font-size를 16px로 통일

```css
/* 수정 전 */
.form-group input { font-size: 14px; }

/* 수정 후 */
.form-group input { font-size: 16px; -webkit-appearance: none; }
```

---

### 2. 하단 네비게이션 active 상태 표시 ✅
**문제:** 모든 페이지에서 하단 네비 아이콘이 항상 회색으로 표시됨 (현재 페이지 구분 불가)
**수정:** 각 HTML 파일 `</body>` 직전에 아래 스크립트 추가

```html
<script>
(function(){
  var path = window.location.pathname;
  document.querySelectorAll('.mobile-bottom-nav a').forEach(function(a){
    var href = a.getAttribute('href');
    if(path === href || (href !== '/' && path.startsWith(href))){
      a.classList.add('active');
    }
  });
})();
</script>
```

**적용 대상 파일:**
- `templates/main.html`
- `templates/upload.html`
- `templates/result_upload.html`
- `templates/crawl.html`
- `templates/result_crawl.html`
- `templates/alerts.html`
- `templates/mypage.html`
- `templates/history.html`

---

### 3. iOS Safe Area (노치/홈바/다이나믹 아일랜드) 대응 ✅
**문제:** iPhone X 이후 기종에서 홈바 영역에 컨텐츠가 가려짐
**수정:** `env(safe-area-inset-*)` 적용

```css
/* 하단 네비 */
.mobile-bottom-nav {
  padding-bottom: max(8px, env(safe-area-inset-bottom));
}
/* body */
body {
  padding-bottom: calc(64px + env(safe-area-inset-bottom));
}
/* navbar 좌우 */
.navbar {
  padding-left: max(28px, env(safe-area-inset-left));
  padding-right: max(28px, env(safe-area-inset-right));
}
/* 로그인 화면 */
.login-card {
  padding-top: max(32px, env(safe-area-inset-top));
  padding-bottom: max(32px, env(safe-area-inset-bottom));
}
```

---

### 4. 가로 모드(Landscape) 최적화 ✅
**문제:** 스마트폰 가로 모드에서 하단 네비가 너무 높고, 로그인 카드가 잘림
**수정:**

```css
@media (max-width: 768px) and (orientation: landscape) {
  .mobile-bottom-nav { padding-top: 4px; }
  .mobile-bottom-nav a { min-height: 36px; }
  .mobile-bottom-nav a span { font-size: 16px; }
  body { padding-bottom: calc(52px + env(safe-area-inset-bottom)); }
  .hero-icon { display: none; }
  .login-bg { align-items: flex-start; overflow-y: auto; }
  .login-card { min-height: auto; margin: 20px auto; }
}
```

---

### 5. 마이페이지 하단 버튼 행 모바일 대응 ✅
**문제:** "로그아웃" / "회원 탈퇴" 버튼이 좁은 화면에서 찌그러짐
**수정:** `mypage.html` 버튼 행에 `flex-wrap: wrap` + `min-width` 추가

```html
<!-- 수정 후 -->
<div style="display:flex;gap:10px;margin-bottom:40px;flex-wrap:wrap;" class="mypage-action-row">
  <a href="/logout" class="btn btn-secondary" style="flex:1;min-width:140px;">로그아웃</a>
  <form method="POST" action="/withdraw" style="flex:1;min-width:140px;" ...>
    <button type="submit" class="btn btn-danger" style="width:100%;">회원 탈퇴</button>
  </form>
</div>
```

480px 이하에서는 CSS로 세로 배치:
```css
@media (max-width: 480px) {
  .mypage-action-row { flex-direction: column !important; }
  .mypage-action-row .btn,
  .mypage-action-row form { width: 100% !important; flex: none !important; }
}
```

---

### 6. 로딩 오버레이 tip-card 모바일 잘림 ✅
**문제:** `crawl.html`, `upload.html`의 분석 대기 화면에서 팁 카드가 화면 밖으로 넘침
**수정:**

```css
@media (max-width: 480px) {
  .tip-card {
    padding: 28px 20px !important;
    margin: 0 12px !important;
  }
  .tip-emoji { font-size: 48px !important; }
  .tip-title { font-size: 18px !important; }
  .tip-desc  { font-size: 13px !important; }
}
```

---

### 7. 하단 네비 블러 효과 (앱스러운 느낌) ✅
**문제:** 하단 네비가 단순 흰 배경으로 앱 느낌이 부족
**수정:** backdrop-filter blur 적용

```css
.mobile-bottom-nav {
  background: rgba(255,255,255,0.96);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
```

---

### 8. 텍스트 자동 크기 조정 방지 ✅
**문제:** 일부 Android 기기에서 브라우저가 텍스트 크기를 임의로 조정
**수정:**

```css
body {
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
}
```

---

### 9. 카드 내 텍스트 넘침 방지 ✅
**문제:** 긴 URL, 이메일 등이 카드 밖으로 넘침
**수정:**

```css
.card { overflow: hidden; }
.info-table td { word-break: break-all; }
.alert-body { min-width: 0; }
.alert-title { word-break: break-all; }
```

---

### 10. 터치 영역 최적화 ✅
**문제:** 작은 버튼/링크가 손가락으로 누르기 어려움 (Apple HIG: 최소 44×44pt)
**수정:**

```css
.btn { min-height: 48px; }
.form-group input, .form-group select { min-height: 48px; }
.navbar .nav-links a { min-height: 44px; }
.mobile-bottom-nav a { min-height: 44px; }
* { -webkit-tap-highlight-color: transparent; }
```

---

## 적용 방법

### style.css 교체
```
static/css/style.css  ←  새 style.css로 교체
```

### 각 HTML 파일에 active 스크립트 추가
`</body>` 직전에 아래 추가:
```html
<script>
(function(){
  var path=window.location.pathname;
  document.querySelectorAll('.mobile-bottom-nav a').forEach(function(a){
    var href=a.getAttribute('href');
    if(path===href||(href!=='/'&&path.startsWith(href))){
      a.classList.add('active');
    }
  });
})();
</script>
```

### mypage.html 교체
```
templates/mypage.html  ←  새 mypage.html로 교체
```

---

## 테스트 체크리스트

- [ ] iPhone Safari: 입력창 포커스 시 줌 없음
- [ ] iPhone Safari: 홈바 영역 컨텐츠 가려짐 없음
- [ ] iPhone: 가로 모드 전환 시 레이아웃 정상
- [ ] 하단 네비: 현재 페이지 파란색으로 표시됨
- [ ] 마이페이지: 로그아웃/탈퇴 버튼 세로 배치됨 (480px 이하)
- [ ] 분석 로딩 화면: 팁 카드 화면 안에 표시됨
- [ ] 긴 URL이 카드 밖으로 넘치지 않음
- [ ] Android Chrome: 텍스트 크기 자동 조정 없음
