-- ============================================================
-- ztb_clean 纯净数据库 — DDL 脚本（Docker 初始化自动执行）
-- 数据库：ztb_clean（由 docker-compose MYSQL_DATABASE 自动创建）
-- ============================================================
-- 表清单（2026-09-04 定稿，仅 3 张在用业务表）：
--   company_info / company_penalty / bid_project
--   product_info 已随产品查询功能线下线（2026-08-10），不再建表；
--   其数据以 raw_tables/product_info.csv 留档作回滚资产。
-- 索引策略：本脚本仅建 BTREE + UNIQUE；FULLTEXT（ngram）在数据导入
-- 后统一构建，规格见文末注释（与 agent/nodes/price_inquiry/schema.py
-- 的 _HARDCODED_SCHEMA semantic 列集合严格一致，MATCH 列集合必须与
-- FULLTEXT 索引定义完全一致）。
-- ============================================================

USE `ztb_clean`;

-- ============================================================
-- 1. company_info（企业工商信息表）
-- ============================================================
CREATE TABLE IF NOT EXISTS `company_info` (
    `id`                             BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_name`                   VARCHAR(256)  NOT NULL COMMENT '企业名称',
    `legal_person`                   VARCHAR(128)  DEFAULT NULL COMMENT '法定代表人',
    `registered_capital`             VARCHAR(64)   DEFAULT NULL COMMENT '注册资本（原始字符串）',
    `registered_capital_amount_cny`  DECIMAL(20,2) DEFAULT NULL COMMENT '注册资本数值(元)',
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
    INDEX `idx_registered_capital_amount` (`registered_capital_amount_cny`),
    INDEX `idx_company_name` (`company_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='企业工商信息表（来源：raw_tables/company_info.csv）';

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
  COMMENT='企业处罚信息表（来源：raw_tables/company_penalty.csv）';

-- ============================================================
-- 3. bid_project（招标项目交易记录表）
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
    INDEX `idx_project_category` (`project_category`),
    INDEX `idx_project_name` (`project_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='招标项目交易记录表（来源：raw_tables/bid_project.csv）';

-- ============================================================
-- FULLTEXT 索引（数据导入后由导入流程统一执行，勿在本脚本中创建）
-- 规格 = _HARDCODED_SCHEMA[table]["semantic"]，全部 WITH PARSER ngram：
-- ALTER TABLE `company_info`
--   ADD FULLTEXT INDEX `ft_company_info` (`company_name`, `business_scope`, `industry`, `address`) WITH PARSER ngram;
-- ALTER TABLE `company_penalty`
--   ADD FULLTEXT INDEX `ft_penalty` (`company_name`, `illegal_behavior`, `penalty_result`) WITH PARSER ngram;
-- ALTER TABLE `bid_project`
--   ADD FULLTEXT INDEX `ft_semantic` (`purchaser`, `successful_bidder`) WITH PARSER ngram;
--   ^ P0-11：bid_project 恰好 2 列；若含 project_name/subject_matter 等列，
--     MATCH(purchaser, successful_bidder) 将无法命中该索引。
-- ============================================================
