-- ============================================================
-- ztb_clean 纯净数据库 — DDL 脚本
-- 数据库：ztb_clean（全新独立，仅从 raw_tables/ CSV 导入）
-- 执行顺序：先建表（含 BTREE + UNIQUE KEY），数据导入后再建 FULLTEXT
-- ============================================================

CREATE DATABASE IF NOT EXISTS `ztb_clean`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `ztb_clean`;

-- ============================================================
-- 1. company_info（企业工商信息表）
-- ============================================================
CREATE TABLE IF NOT EXISTS `company_info` (
    `id`                             BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_name`                   VARCHAR(256)  NOT NULL COMMENT '企业名称',
    `legal_person`                   VARCHAR(128)  DEFAULT NULL COMMENT '法定代表人',
    `registered_capital`             VARCHAR(64)   DEFAULT NULL COMMENT '注册资本（原始字符串）',
    `registered_capital_amount_cny`  DECIMAL(20,2) DEFAULT NULL COMMENT '注册资本数值(元) — 由导入脚本自动换算',
    `establish_date`                 DATE          DEFAULT NULL COMMENT '成立日期',
    `business_status`                VARCHAR(64)   DEFAULT NULL COMMENT '经营状态',
    `province`                       VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`                           VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `district`                       VARCHAR(64)   DEFAULT NULL COMMENT '区县',
    `industry`                       VARCHAR(128)  DEFAULT NULL COMMENT '所属行业',
    `company_type`                   VARCHAR(64)   DEFAULT NULL COMMENT '企业类型',
    `credit_code`                    VARCHAR(64)   DEFAULT NULL COMMENT '统一社会信用代码（主去重键）',
    `address`                        VARCHAR(512)  DEFAULT NULL COMMENT '企业地址',
    `credit_rating`                  VARCHAR(64)   DEFAULT NULL COMMENT '信用评级',
    `company_level`                  VARCHAR(64)   DEFAULT NULL COMMENT '企业等级',
    `business_scope`                 TEXT          DEFAULT NULL COMMENT '经营范围',
    `source_file`                    VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`                     DATETIME      DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_credit_code` (`credit_code`),
    INDEX `idx_province` (`province`),
    INDEX `idx_city` (`city`),
    INDEX `idx_industry` (`industry`),
    INDEX `idx_company_level` (`company_level`),
    INDEX `idx_business_status` (`business_status`),
    INDEX `idx_registered_capital_amount` (`registered_capital_amount_cny`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='企业工商信息表 — 来源：raw_tables/company_info.csv';

-- FULLTEXT 索引（数据导入后执行）:
-- ALTER TABLE `company_info`
--   ADD FULLTEXT INDEX `ft_company_info` (`company_name`, `business_scope`, `industry`, `address`) WITH PARSER ngram;


-- ============================================================
-- 2. company_penalty（企业处罚信息表）
-- ============================================================
CREATE TABLE IF NOT EXISTS `company_penalty` (
    `id`                    BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_name`          VARCHAR(256)  NOT NULL COMMENT '企业名称',
    `credit_code`           VARCHAR(64)   DEFAULT NULL COMMENT '统一社会信用代码（关联 company_info）',
    `penalty_date`          DATE          DEFAULT NULL COMMENT '处罚日期',
    `law_enforcement_unit`  VARCHAR(256)  DEFAULT NULL COMMENT '执法单位',
    `illegal_behavior`      TEXT          DEFAULT NULL COMMENT '违法行为',
    `penalty_result`        TEXT          DEFAULT NULL COMMENT '处罚结果',
    `source_file`           VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`            DATETIME      DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_company_name` (`company_name`),
    INDEX `idx_credit_code` (`credit_code`),
    INDEX `idx_penalty_date` (`penalty_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='企业处罚信息表 — 来源：raw_tables/company_penalty.csv';

-- FULLTEXT 索引（数据导入后执行）:
-- ALTER TABLE `company_penalty`
--   ADD FULLTEXT INDEX `ft_penalty` (`company_name`, `illegal_behavior`, `penalty_result`) WITH PARSER ngram;


-- ============================================================
-- 3. product_info（产品市场行情表）
-- ============================================================
CREATE TABLE IF NOT EXISTS `product_info` (
    `id`                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    `product_name`        VARCHAR(500)  DEFAULT NULL COMMENT '产品名称',
    `supplier_name`       VARCHAR(256)  DEFAULT NULL COMMENT '供应商名称',
    `price`               DECIMAL(20,2) DEFAULT NULL COMMENT '报价（元）',
    `price_unit`          VARCHAR(32)   DEFAULT NULL COMMENT '计价单位',
    `currency`            VARCHAR(16)   DEFAULT 'CNY' COMMENT '货币',
    `category`            VARCHAR(256)  DEFAULT NULL COMMENT '产品大类',
    `subcategory`         VARCHAR(256)  DEFAULT NULL COMMENT '产品小类',
    `product_parameters`  TEXT          DEFAULT NULL COMMENT '产品参数',
    `min_order_qty`       INT           DEFAULT NULL COMMENT '起订量',
    `province`            VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`                VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `supplier_address`    VARCHAR(512)  DEFAULT NULL COMMENT '供应商地址',
    `contact_person`      VARCHAR(64)   DEFAULT NULL COMMENT '联系人',
    `contact_info`        VARCHAR(128)  DEFAULT NULL COMMENT '联系方式（电话）',
    `source_file`         VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_product_name` (`product_name`),
    INDEX `idx_supplier_name` (`supplier_name`),
    INDEX `idx_price` (`price`),
    INDEX `idx_category` (`category`),
    INDEX `idx_province` (`province`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='产品市场行情表 — 来源：raw_tables/product_info.csv';

-- FULLTEXT 索引（数据导入后执行）:
-- ALTER TABLE `product_info`
--   ADD FULLTEXT INDEX `ft_product` (`product_name`, `supplier_name`, `product_parameters`, `category`) WITH PARSER ngram;


-- ============================================================
-- 4. bid_project（招标项目交易记录表）
-- ============================================================
CREATE TABLE IF NOT EXISTS `bid_project` (
    `id`                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    `project_number`     VARCHAR(128)  DEFAULT NULL COMMENT '项目编号',
    `project_name`       VARCHAR(500)  DEFAULT NULL COMMENT '项目名称',
    `purchaser`          VARCHAR(256)  DEFAULT NULL COMMENT '采购人/招标单位',
    `agent`              VARCHAR(256)  DEFAULT NULL COMMENT '代理机构',
    `budget_amount`      DECIMAL(20,2) DEFAULT NULL COMMENT '预算金额（元）',
    `winning_amount`     DECIMAL(20,2) DEFAULT NULL COMMENT '中标金额（元）',
    `successful_bidder`  VARCHAR(500)  DEFAULT NULL COMMENT '中标供应商',
    `winning_date`       DATE          DEFAULT NULL COMMENT '中标日期',
    `subject_matter`     VARCHAR(500)  DEFAULT NULL COMMENT '标的物',
    `province`           VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`               VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `district`           VARCHAR(64)   DEFAULT NULL COMMENT '区县',
    `project_category`   VARCHAR(128)  DEFAULT NULL COMMENT '项目类别',
    `project_stage`      VARCHAR(64)   DEFAULT NULL COMMENT '项目阶段（结果公告/招标公告/更正公告等）',
    `publish_date`       DATE          DEFAULT NULL COMMENT '发布日期',
    `source_file`        VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `source_url`         VARCHAR(1024) DEFAULT NULL COMMENT '来源链接',
    `created_at`         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_project_number` (`project_number`),
    INDEX `idx_purchaser` (`purchaser`),
    INDEX `idx_successful_bidder` (`successful_bidder`),
    INDEX `idx_winning_date` (`winning_date`),
    INDEX `idx_winning_amount` (`winning_amount`),
    INDEX `idx_province` (`province`),
    INDEX `idx_project_stage` (`project_stage`),
    INDEX `idx_publish_date` (`publish_date`),
    INDEX `idx_project_category` (`project_category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='招标项目交易记录表 — 来源：raw_tables/bid_project.csv';

-- FULLTEXT 索引（数据导入后执行）:
-- ALTER TABLE `bid_project`
--   ADD FULLTEXT INDEX `ft_bid_project` (`project_name`, `purchaser`, `successful_bidder`, `subject_matter`) WITH PARSER ngram;
