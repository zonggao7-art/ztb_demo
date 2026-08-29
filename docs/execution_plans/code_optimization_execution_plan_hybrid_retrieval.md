检索与入库代码先行优化计划

结论

可以先不动数据库，而且这是更安全的小步顺序：

先优化代码结构与契约
→ 使用 Fake/Mock 做离线自动验证
→ 再并行部署新版 Milvus
→ 最后用少量数据验证真实 BM25/hybrid

但要明确边界：离线阶段可以证明代码的分支、错误处理和数据契约正确，不能证明特定 Milvus 版本的 BM25 Function、analyzer、索引和 raw-text hybrid API 一定兼容；这些必须留到真实新版 Milvus 集成测试确认。

全程不连接或修改当前生产 public_kb，当前 Milvus 2.4.0 继续提供旧 dense 检索。

直接复用范围





public_kb/rag_engine.py：保留 RAG 门面、query() 与问答链构建。



public_kb/embedding_service.py：保留 BAAI/bge-m3 dense Embedding 封装。



public_kb/csv_loader.py、public_kb/text_cleaner.py、public_kb/chunker.py：本阶段不改。



public_kb/citations.py：保留 Citation 和 R1-R7 契约。



agent/nodes/knowledge_qa.py：保持外部业务接口不变。

主要优化限定为 public_kb/config.py、public_kb/milvus_store.py、public_kb/qa_chain.py 和测试代码。

阶段 A：完全离线的代码优化

A1. 冻结最小输入输出契约

不引入庞大的新领域模型，仅明确现有结构：

入库输入

继续使用 List[Document]，每项要求：





page_content 非空；



metadata 至少含 doc_name/chapter/chunk_index；



chunk_uid 可缺省，由现有 compute_chunk_uid() 补齐；



dense embedding 数量必须与文档数量一致，单向量维度必须等于 embedding_dim。

Milvus 集合契约





主键 id；



文本字段 text 且启用 analyzer；



dense 字段 vector；



BM25 输出字段 sparse_vector；



BM25 Function 输入 text、输出 sparse_vector；



dense index metric COSINE；



sparse index metric BM25。

检索输入输出





输入继续为字符串 question，新增空字符串校验；



内部检索结果继续使用 List[Tuple[Document, float]]，避免牵动 Citation；



外部继续返回 answer/sources/citations/citation_validation；



可附加 retrieval_diagnostics，不破坏已有调用方。

错误与降级契约

明确区分：配置错误、集合不存在、Schema 不兼容、连接错误、Embedding 失败、hybrid 失败、Reranker 失败和无召回结果。生产模式允许分层降级，严格验证模式禁止 hybrid 失败后伪装成功。

验证：使用 dataclass/Settings 构造和纯字典样例做契约单测，不连接数据库。

A2. 优化 config.py





增加显式 milvus_uri，同时保留 host/port 兼容当前 2.4.0。



增加 BM25 开关、实验集合名、字段名、Function 名、Schema 版本、连接 timeout、严格验证模式。



验证模式下集合名必须匹配实验前缀，禁止指向 public_kb。



索引类型和 BM25 参数配置化，但先提供官方基线默认值，不在离线阶段假定性能最优。



不在日志或异常中输出认证秘密。

离线测试：环境变量覆盖、默认值、非法集合名、字段冲突和 URI 生成。

A3. 优化 milvus_store.py

保留





MilvusStoreManager 单类，不建设复杂 Repository 层；



_batch_insert() dense 批量 Embedding；



metadata 透传；



chunk_uid；



LangChain wrapper；



add_documents/load_existing/clear_collection 对外接口。

调整





构造函数支持注入 client，默认才创建真实 MilvusClient；单测注入 Fake Client。



提取 _build_schema()、_build_index_params()、_validate_collection_contract() 和 _build_records() 小方法。



_build_schema() 描述 analyzer、sparse 字段和 BM25 Function；离线通过 Fake Schema 断言方法调用及参数。



_build_index_params() 描述 dense/COSINE 与 sparse/BM25 索引；索引类型配置化。



initialize_collection(documents, recreate=False) 默认不删除已有集合；仅 recreate=True 且实验集合名安全时允许 drop。



空 documents、空文本、Embedding 数量不一致、维度不一致、超长字段处理均显式校验。



创建后必须 _validate_collection_contract()，失败时不 insert。



load_existing() 区分集合不存在、连接错误、Schema 不兼容；不再所有异常都返回 False。



原生 Client 和 LangChain wrapper 使用同一 URI/认证参数。



sparse 内容不由应用生成；record 只包含 text + vector + metadata。

离线测试通过 Fake Client 记录：create_schema、add_field、add_function、add_index、create_collection、insert、flush、load、drop 的调用顺序和参数。

A4. 优化 qa_chain.py

保留





LCEL 链；



prompt；



_entity_to_doc()；



search/hybrid_search helper；



AnnSearchRequest + RRFRanker；



_dense_only_retrieve()；



Citation 和拒答逻辑。

调整





对 BM25 Function sparse 路使用明确的 BM25 请求构造，不再复用外部 sparse vector 的 IP 语义。



将 query vector 作为可选参数传给 dense fallback，避免 hybrid 失败后重复 embed_query()。



将“检索执行结果”和 diagnostics 放在一个内部轻量结构中，外部回答契约不变。



区分 output fields 兼容回退与真实 hybrid/连接错误。



_SiliconFlowReranker 支持注入 HTTP session/client，便于 Mock；失败返回明确状态而不是伪造 0.5。



Reranker 成功才使用 _adaptive_threshold()；失败时保留 RRF 顺序，使用独立的 Top-K 截断，不把 RRF score 当 Reranker 分数。



diagnostics 至少包含：retrieval_mode、dense_count、sparse_count、fusion_count、reranker_status、threshold、fallback_reason。



严格验证模式下 hybrid 异常向测试暴露；生产模式才进入 dense fallback。



Schema 能力通过构建链参数或缓存注入，避免每次查询调用 describe；保留显式重新探测入口供集成测试。



空 question 在调用 Embedding 前失败或返回明确拒答。

离线测试使用 Fake MilvusClient/Fake Embeddings/Mock Reranker 覆盖所有分支。

阶段 B：离线测试门禁

在不访问任何真实 Milvus 的情况下完成：

B1. milvus_store 单元测试





正确 Schema 字段和 BM25 Function；



正确 dense/sparse 索引参数；



默认不 drop；



非实验集合即使 recreate=True 也拒绝 drop；



Schema 校验失败不 insert；



空文档、Embedding 数量/维度不一致失败；



record 不包含客户端 sparse 内容；



metadata 和 chunk_uid 正确；



Client 与 wrapper 连接参数一致。

B2. qa_chain 单元测试





dense + sparse 请求均构造；



hybrid + RRF 命中路径；



已生成 dense vector 被 fallback 复用；



sparse 缺失的生产降级；



hybrid 失败的严格模式；



output fields 回退；



Reranker 成功与真实阈值；



Reranker 失败保留 RRF 排序且无假分；



无结果拒答；



diagnostics 与实际路径一致；



Citation 返回契约不变。

B3. 静态门禁





linter 无新增错误；



新增代码不读取真实 .env 密钥做单测；



单测不能连接当前 MILVUS_HOST；



生产模块不含 PoC 固定 IP、端口或测试集合名；



现有 dense-only 路径回归测试通过。

只有阶段 B 全部通过，才进入数据库升级和真实验证。

阶段 C：数据库升级与真实集成

C1. 并行部署新版 Milvus





先检查当前部署方式、etcd、MinIO、卷、资源和备份恢复。



选择支持 BM25 的固定 Milvus patch 版本，优先评估受维护的 2.6.x；不使用 latest。



在独立端口、独立 etcd、独立对象存储路径和独立数据卷部署新版实例；旧 2.4.0 保持运行。



使用临时集合验证 analyzer、BM25 Function、索引、raw-text search 和 hybrid API。



根据真实结果微调 A 阶段中版本相关参数；不得为了“让测试通过”绕过契约校验。

C2. 小样本真实入库





选择 30～100 个清洗后代表性 chunk；不做全量写入。



使用 BAAI/bge-m3 生成 dense vectors。



写入隔离实验集合；由 Milvus BM25 Function 自动生成 sparse。



校验行数、Schema、Function、索引、回表和重启持久性。

C3. 分层混合检索验证





dense 单路；



BM25 单路；



hybrid + RRF；



Reranker 成功；



Reranker 失败降级；



无答案拒答；



citations/R1-R7；



diagnostics 必须证明没有静默 dense fallback。

离线阶段不能下结论的事项

以下内容必须等待真实新版 Milvus：





pymilvus 与目标服务端是否完整兼容；



describe_collection() 中 Function/analyzer 的实际返回结构；



BM25 sparse request 的精确参数是否被目标版本接受；



output_fields=["*"] 对 BM25 内部 sparse 字段的行为；



中文 analyzer 的真实切词与召回质量；



索引创建、load、flush、重启持久化；



网络延迟和性能。

因此先优化代码是可行的，但不能以离线测试通过代替真实数据库集成验证。

推荐执行顺序

A1 契约
→ A2 配置
→ A3 入库管理器
→ A4 检索链
→ B 离线测试门禁
→ C1 并行新版 Milvus
→ C2 小样本入库
→ C3 真实混合检索验证

每一步仅修改一个可验证边界；阶段 A/B 不访问、不删除、不写入当前云端 Milvus。