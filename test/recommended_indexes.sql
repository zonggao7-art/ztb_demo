-- ============================================================
-- 价格查询/中标检索优化索引建议（硬过滤 + 混合检索）
-- 运行前请确认：
--   1. MySQL >= 8.0.18 且 InnoDB 支持 FULLTEXT
--   2. 服务器已启用 ngram 解析器（推荐在 my.cnf 中设置 ngram_token_size=2）
--   3. 大表建索引请在业务低峰期执行，避免长时间锁表
-- ============================================================

-- xunfei_202605_01
ALTER TABLE `xunfei_202605_01`.`ods_tender`
    ADD FULLTEXT INDEX `ft_ods_tender_title_name_procurement_title` (`title`, `name`, `procurement_title`) WITH PARSER ngram;
ALTER TABLE `xunfei_202605_01`.`ods_company_detail`
    ADD FULLTEXT INDEX `ft_ods_company_detail_name` (`name`) WITH PARSER ngram;
ALTER TABLE `xunfei_202605_01`.`ods_public_opinion`
    ADD FULLTEXT INDEX `ft_ods_public_opinion_title_article_text` (`title`, `article_text`) WITH PARSER ngram;
ALTER TABLE `xunfei_202605_01`.`ods_products`
    ADD FULLTEXT INDEX `ft_ods_products_material_name_supplier_name` (`material_name`, `supplier_name`) WITH PARSER ngram;
ALTER TABLE `xunfei_202605_01`.`ods_policy`
    ADD FULLTEXT INDEX `ft_ods_policy_rule_title_content` (`rule_title`, `content`) WITH PARSER ngram;

-- bidding_information_dai
ALTER TABLE `bidding_information_dai`.`companies`
    ADD FULLTEXT INDEX `ft_companies_company_name` (`company_name`) WITH PARSER ngram;
ALTER TABLE `bidding_information_dai`.`goods_info`
    ADD FULLTEXT INDEX `ft_goods_info_material_name_supplier_name` (`material_name`, `supplier_name`) WITH PARSER ngram;
ALTER TABLE `bidding_information_dai`.`notifications`
    ADD FULLTEXT INDEX `ft_notifications_project_name_title_content` (`project_name`, `title`, `content`) WITH PARSER ngram;
ALTER TABLE `bidding_information_dai`.`projects`
    ADD FULLTEXT INDEX `ft_projects_project_name_approval_name_department_name` (`project_name`, `approval_name`, `department_name`) WITH PARSER ngram;
ALTER TABLE `bidding_information_dai`.`procurement_notices`
    ADD FULLTEXT INDEX `ft_procurement_notices_project_name_title_content` (`project_name`, `title`, `content`) WITH PARSER ngram;

-- xunfei5
ALTER TABLE `xunfei5`.`ods_tender`
    ADD FULLTEXT INDEX `ft_ods_tender_title_name_procurement_title` (`title`, `name`, `procurement_title`) WITH PARSER ngram;
ALTER TABLE `xunfei5`.`ods_company_detail`
    ADD FULLTEXT INDEX `ft_ods_company_detail_name` (`name`) WITH PARSER ngram;
ALTER TABLE `xunfei5`.`ods_public_opinion`
    ADD FULLTEXT INDEX `ft_ods_public_opinion_title_article_text` (`title`, `article_text`) WITH PARSER ngram;
ALTER TABLE `xunfei5`.`ods_products`
    ADD FULLTEXT INDEX `ft_ods_products_material_name_supplier_name` (`material_name`, `supplier_name`) WITH PARSER ngram;
ALTER TABLE `xunfei5`.`ods_policy`
    ADD FULLTEXT INDEX `ft_ods_policy_rule_title_content` (`rule_title`, `content`) WITH PARSER ngram;
ALTER TABLE `xunfei5`.`ods_policy_regulation_files`
    ADD FULLTEXT INDEX `ft_ods_policy_regulation_files_title_file_name_content` (`title`, `file_name`, `content`) WITH PARSER ngram;

-- xunfei_06
ALTER TABLE `xunfei_06`.`tender`
    ADD FULLTEXT INDEX `ft_tender_tender_title_source_name_content` (`tender_title`, `source_name`, `content`) WITH PARSER ngram;
ALTER TABLE `xunfei_06`.`product`
    ADD FULLTEXT INDEX `ft_product_title_content_supplier_name` (`title`, `content`, `supplier_name`) WITH PARSER ngram;
ALTER TABLE `xunfei_06`.`news`
    ADD FULLTEXT INDEX `ft_news_news_title_content` (`news_title`, `content`) WITH PARSER ngram;
ALTER TABLE `xunfei_06`.`enterprise`
    ADD FULLTEXT INDEX `ft_enterprise_enterprise_name_content` (`enterprise_name`, `content`) WITH PARSER ngram;
ALTER TABLE `xunfei_06`.`laws`
    ADD FULLTEXT INDEX `ft_laws_document_title_chapter_title_section_title` (`document_title`, `chapter_title`, `section_title`) WITH PARSER ngram;
ALTER TABLE `xunfei_06`.`policy`
    ADD FULLTEXT INDEX `ft_policy_policy_title_content` (`policy_title`, `content`) WITH PARSER ngram;

-- tm（部分中文表名建议先改名或保留原名执行）
ALTER TABLE `tm`.`招标采购历史数据`
    ADD FULLTEXT INDEX `ft_zbcgls_title_project_subject` (`标题`, `项目名称`, `标的物`) WITH PARSER ngram;
ALTER TABLE `tm`.`物资商品信息表`
    ADD FULLTEXT INDEX `ft_wzspxx_material_supplier` (`material_name`, `supplier_name`) WITH PARSER ngram;
ALTER TABLE `tm`.`notifications`
    ADD FULLTEXT INDEX `ft_notifications_project_name_title_content` (`project_name`, `title`, `content`) WITH PARSER ngram;
ALTER TABLE `tm`.`福建省政府招投标交易`
    ADD FULLTEXT INDEX `ft_fjzzgzt_content_project` (`content`, `Proj Name`) WITH PARSER ngram;
