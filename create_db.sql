-- ① 데이터베이스 생성
CREATE DATABASE IF NOT EXISTS codeshot_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE codeshot_db;

-- ② tb_user
CREATE TABLE IF NOT EXISTS tb_user (
    id         VARCHAR(50)  NOT NULL COMMENT '사용자 아이디',
    pwd        VARCHAR(255) NOT NULL COMMENT '사용자 비밀번호 (SHA-256 해시)',
    name       VARCHAR(50)  NOT NULL COMMENT '사용자 이름',
    email      VARCHAR(50)  NOT NULL COMMENT '사용자 이메일',
    phone      VARCHAR(20)  NOT NULL COMMENT '사용자 연락처',
    role       VARCHAR(10)  NOT NULL DEFAULT 'user' COMMENT '권한 (user/admin)',
    joined_at  DATETIME     NOT NULL DEFAULT NOW() COMMENT '가입일자',
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS UQ_tb_user_1 ON tb_user (email, phone);

-- ③ tb_upload
CREATE TABLE IF NOT EXISTS tb_upload (
    upload_idx   INT          NOT NULL AUTO_INCREMENT,
    id           VARCHAR(50)  NOT NULL,
    file_name    VARCHAR(50)  NOT NULL,
    file_size    VARCHAR(50)  NOT NULL,
    file_ext     VARCHAR(10)  NOT NULL,
    uploaded_at  DATETIME     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (upload_idx),
    FOREIGN KEY (id) REFERENCES tb_user(id) ON DELETE CASCADE
);

-- ④ tb_deep_upload
CREATE TABLE IF NOT EXISTS tb_deep_upload (
    deep_idx    INT      NOT NULL AUTO_INCREMENT,
    upload_idx  INT      NOT NULL,
    deep_model  VARCHAR(50)  NOT NULL DEFAULT 'Gemini 2.5 Flash',
    deep_result TEXT     NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT NOW(),
    PRIMARY KEY (deep_idx),
    FOREIGN KEY (upload_idx) REFERENCES tb_upload(upload_idx) ON DELETE CASCADE
);

-- ⑤ tb_crawling
CREATE TABLE IF NOT EXISTS tb_crawling (
    cr_idx      INT          NOT NULL AUTO_INCREMENT,
    id          VARCHAR(50)  NOT NULL,
    cr_url      VARCHAR(255) NOT NULL,
    cr_col1     TEXT         NOT NULL,
    cr_col2     TEXT         NOT NULL,
    cr_col3     TEXT         NOT NULL,
    cr_col4     TEXT         NOT NULL,
    cr_col5     TEXT         NOT NULL,
    cr_col6     TEXT         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cr_idx),
    FOREIGN KEY (id) REFERENCES tb_user(id) ON DELETE CASCADE
);

-- ⑥ tb_deep_crawling
CREATE TABLE IF NOT EXISTS tb_deep_crawling (
    deep_idx        INT           NOT NULL AUTO_INCREMENT,
    crawling_idx    INT           NOT NULL,
    fishing_phrase  VARCHAR(1000) NOT NULL,
    check_url       VARCHAR(50)   NOT NULL,
    total_analysis  TEXT          NOT NULL,
    ciritical_level VARCHAR(50)   NOT NULL,
    created_at      DATETIME      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (deep_idx),
    FOREIGN KEY (crawling_idx) REFERENCES tb_crawling(cr_idx) ON DELETE CASCADE
);

-- ⑦ tb_alert
CREATE TABLE IF NOT EXISTS tb_alert (
    alert_idx    INT          NOT NULL AUTO_INCREMENT,
    id           VARCHAR(50)  NOT NULL,
    alert_type   VARCHAR(20)  NOT NULL COMMENT '업로드 또는 크롤링',
    idx_no       INT          NULL,
    deep_idx     INT          NOT NULL,
    alert_msg    TEXT         NOT NULL,
    sended_at    DATETIME     NOT NULL,
    received_yn  CHAR(1)      NOT NULL DEFAULT 'N',
    received_at  DATETIME     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (alert_idx),
    FOREIGN KEY (id) REFERENCES tb_user(id) ON DELETE CASCADE
);
