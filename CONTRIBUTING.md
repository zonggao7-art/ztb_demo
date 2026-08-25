# 贡献指南 / 开发规范

> 本文是 **ztb_demo** 项目的开发者工作流指南。覆盖从日常改代码 → 提 PR → 合并 → 排查问题 的完整流程。
>
> 📌 阅读对象：项目所有成员（owner + collaborator）
>
> 🔗 仓库：https://github.com/zonggao7-art/ztb_demo
>
> 🛡️ 保护规则：`main` 分支受保护，禁止直接 push，必须走 PR + 至少 1 人 review。

---

## 一、新成员第一次接入（5 步）

如果你还没 clone 过这个仓库，按下面顺序来。已经 clone 的请跳到 **第二章**。

### 1.1 配置 SSH key

如果你的电脑上还没有 GitHub SSH key：

```bash
ssh-keygen -t ed25519 -C "你的邮箱"
# 一路回车，密码留空
clip < ~/.ssh/id_ed25519.pub
```

把剪贴板里的内容粘贴到 https://github.com/settings/keys → **New SSH key** → 保存。

验证：

```bash
ssh -T git@github.com
```

应看到：`Hi 你的用户名! You've successfully authenticated, but GitHub does not provide shell access.`

### 1.2 Clone 仓库

```bash
git clone git@github.com:zonggao7-art/ztb_demo.git
cd ztb_demo
```

### 1.3 配置环境变量

```bash
cp .env.example .env
```

用编辑器打开 `.env`，填入你自己的 API key（DeepSeek / SiliconFlow / MinerU / MySQL 密码 / Milvus 等）。**没有 key 的话去对应官网注册申请**。

### 1.4 安装依赖

```bash
pip install -r requirements.txt
```

> ⚠️ 如果报 `ModuleNotFoundError: No module named 'pkg_resources'`，先执行 `pip install 'setuptools<70'`。

### 1.5 拿数据文件 + 验证

向项目 owner 要以下目录/文件，放到仓库根目录：

```
DATA/  raw_pdfs/  new_pdfs/  raw_policy/  raw_tables/
cloud_sync/  *.jsonl
```

验证：

```bash
python -m agent --question "招标方式有哪些？"
```

应看到正常的 AI 回答。

---

## 二、日常改代码的标准流程（核心）

**改任何代码前都按这个顺序来**：

```
   ┌──────────────────┐
   │ 1. 同步 main     │ ← 每天开工必做
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 2. 新建分支      │ ← 从 main 拉
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 3. 改代码 + 测   │ ← 改完先跑相关测试
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 4. add + commit  │ ← commit message 写清楚
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 5. push 自己分支 │ ← 推 origin/feat-xxx，不是 origin/main
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 6. 在 GitHub 提PR│ ← 等 owner review
   └──────────────────┘
            ↓
   ┌──────────────────┐
   │ 7. 合并 + 清理   │ ← Squash merge，删本地分支
   └──────────────────┘
```

### 完整示例

```bash
# 1. 同步 main
git checkout main
git pull origin main

# 2. 新建分支
git checkout -b feat/optimize-router-prompt

# 3. 改代码：用 VS Code 打开 agent/router.py，修改路由 prompt# 4. 跑相关测试
pytest test/test_sub_route.py -v

# 5. 暂存 + 提交
git status
git diff
git add agent/router.py
git commit -m "feat(router): 优化路由 prompt v2

- 调整对'价格类'问题的识别权重
- 补充 3 个测试用例"

# 6. 推到自己分支
git push origin feat/optimize-router-prompt

# 7. 在 GitHub 上提 PR：
#    访问 https://github.com/zonggao7-art/ztb_demo
#    点 "Compare & pull request" → 填描述 → Create pull request
#    等 owner approve
```

---

## 三、分支管理规范

### 3.1 分支命名

| 命名 | 用途 | 示例 |
|---|---|---|
| `feat/xxx` | 新功能 | `feat/add-citation-tracing` |
| `fix/xxx` | 修 bug | `fix/recall-empty-result` |
| `docs/xxx` | 文档/注释 | `docs/update-readme` |
| `refactor/xxx` | 重构（不改外部行为） | `refactor/extract-price-builder` |
| `test/xxx` | 加测试 | `test/cover-router-edge-cases` |
| `chore/xxx` | 杂项（依赖、CI 配置） | `chore/upgrade-pymilvus` |

**命名规则**：
- 全小写、连字符 `-` 分隔
- 动词开头（`add-`、`fix-`、`upgrade-`）
- 不超过 50 字符
- 不用中文

### 3.2 分支生命周期

```bash
# 创建
git checkout -b feat/xxx main         # 从 main 拉新分支

# 工作（多次 commit 都推到这个分支）
git push origin feat/xxx

# PR 合并后清理
git checkout main
git pull origin main
git branch -d feat/xxx               # 删本地分支（-d 安全；-D 强制）
# 远端分支在 GitHub 上手动 Delete（PR 合并时会自动提示）
```

---

## 四、Commit message 规范

采用 [Conventional Commits](https://www.conventionalcommits.org/) 简化版：

```
<type>(<scope>): <一句话描述>

<可选的详细说明>
```

### Type 取值

| type | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能 | `feat: 加 PDF 上传 API` |
| `fix` | 修 bug | `fix: 修复 price_inquiry 空结果` |
| `docs` | 文档/注释 | `docs: 更新 README` |
| `refactor` | 重构 | `refactor: 抽取 SQL builder` |
| `test` | 加测试 | `test: 覆盖 router 边界场景` |
| `chore` | 杂项 | `chore: 升级 pymilvus 到 2.4.3` |
| `perf` | 性能优化 | `perf: cache embedding` |
| `style` | 代码格式（不改语义） | `style: 修复 flake8 警告` |

### Scope 建议（本项目）

- `router` — agent/router.py
- `state` — agent/state.py
- `graph` — agent/graph.py
- `kb` / `rag` — public_kb/
- `price` — agent/nodes/price_inquiry/
- `knowledge` — agent/nodes/knowledge_qa.py
- `docs` — docs/
- `ci` — .github/
- `deps` — requirements.txt

### 好的 commit 示例

```
feat(router): 优化意图分类的 few-shot 提示

- 新增 4 个边界场景的示例
- temperature 调到 0.2
- 单元测试 8/8 通过
```

### 不好的 commit

```
update
fix bug
改了点东西
WIP
```

---

## 五、PR（Pull Request）流程

### 5.1 提 PR

推完分支后，GitHub 会显示黄色提示 **"Compare & pull request"**，点进去：

**Title**：用和 commit message 一致的格式，例如 `feat(router): 优化路由 prompt v2`

**Description 模板**：

```markdown
## 改动
- 改了什么（1-3 条）

## 测试
- [ ] pytest 通过
- [ ] 手动跑了真实问答，路由正确

## 关联
- 关闭 #Issue号（如有）
- 相关 PR #xxx（如有）

## 截图 / 日志（如有）
```

点 **Create pull request**。

### 5.2 Review PR（owner 或其他成员）

进 PR 页面 → **Files changed** → 看 diff：

| 看哪里 | 看什么 |
|---|---|
| `agent/router.py` | prompt 是否合理？路由逻辑覆盖了所有 intent 吗？ |
| `agent/nodes/price_inquiry/` | SQL 是否走索引？有 LIKE 全表扫吗？ |
| `public_kb/` | 检索召回合理吗？reranker 阈值是否合适？ |
| 测试文件 | 新代码有覆盖吗？ |
| `.env.example` | 没漏新环境变量？ |

**评论方式**：
- 对单行评论：点行号左侧的 `+`
- 整体评论：在 **Conversation** 区

**给出 Review 结果**：

```bash
Review changes →
  ◉ Approve          # 同意合并
  ◉ Request changes  # 需要改
  ◉ Comment           # 仅评论（不阻塞合并）
Submit review
```

### 5.3 合并 PR

确认 **Approve ≥ 1** 后：

1. 进 PR 页面 → 点 **Squash and merge**（之前仓库设置里已选为唯一合并方式）
2. 编辑 commit message（如需）
3. 点 **Confirm squash and merge**
4. 等出现 "Merged" 提示 → 点 **Delete branch**（清理远端分支）

### 5.4 合并后清理本地

```bash
git checkout main
git pull origin main
git branch -d feat/xxx              # -d 安全（未合并会拒绝）；-D 强制
```

---

## 六、测试规范

### 6.1 改了什么 → 跑什么

| 改了哪部分 | 必跑测试 |
|---|---|
| `agent/router.py` | `pytest test/test_sub_route.py -v` |
| `agent/nodes/price_inquiry/` 任何文件 | `pytest test/test_recall_optimization.py test/test_bug_repairs.py -v` + 改 SQL 跑 `python test/explain_sql.py --db ztb_clean --sql "<SQL>"` |
| `agent/nodes/knowledge_qa.py` | `pytest test/test_citation_tracing.py -v` |
| `public_kb/` | `python scripts/run_knowledge_citation_eval.py` |
| 任何 node 的 prompt | `python -m agent --question "<相关问题>" --verbose` 至少 3 轮 |
| 任何 SQL | 必跑 EXPLAIN + 全量 `pytest test/ -v` |

### 6.2 提交前自检清单

- [ ] 代码改动对应的测试都跑了
- [ ] 没有遗留 `print()` / `breakpoint()` / 调试代码
- [ ] 没有提交 `.env`、大文件、`__pycache__/`
- [ ] commit message 符合规范
- [ ] 分支名符合规范

---

## 七、常见任务速查

### 7.1 加一个新的 Agent 节点

适用：新增一种业务能力（比如"中标候选人分析"）

1. 创建文件 `agent/nodes/bid_candidate.py`，实现 `(AgentState) -> dict` 接口
2. 在 `agent/router.py` 的 `RouterIntent` Literal 加新值
3. 在 `agent/router.py` 加对应的 routing tool
4. 在 `agent/graph.py` 注册新节点 + 路由分支
5. 写测试 `test/test_bid_candidate.py`
6. 跑 `pytest test/ -v`

详见 [CLAUDE.md §Node interface contract](CLAUDE.md)。

### 7.2 加一个新 PDF 到知识库

```bash
# 把 PDF 放到 raw_pdfs/
cp /path/to/new_law.pdf raw_pdfs/

# 全量重建（首次或大改）
python -m public_kb --init --pdf-dir raw_pdfs

# 或单文件增量
python -c "from public_kb import PublicKnowledgeRAG; PublicKnowledgeRAG().add_pdf('raw_pdfs/new_law.pdf')"
```

### 7.3 改一个 prompt

1. 找到对应 node（如 `agent/router.py`）
2. 改 prompt
3. 跑相关的单元测试
4. 用 `python -m agent --interactive` 手动测 5-10 个真实问题
5. 在 PR 描述里附上对比结果

### 7.4 修一个 bug

1. 复现：写一个失败的测试 `test/test_xxx_bug.py`
2. 跑测试，确认失败（红）
3. 改代码
4. 跑测试，确认通过（绿）
5. 提交：`fix(scope): 修复 xxx 问题`
6. 在 PR 描述里附上"复现 → 修复"对比

### 7.5 加一个新的 SQL 查询路径

1. 在 `agent/nodes/price_inquiry/queries.py` 加新函数
2. 在 `sql_builders.py` 配 SQL 构造
3. 在 `agent/nodes/price_inquiry/__init__.py` 导出
4. **必须先跑 EXPLAIN**：

```bash
python test/explain_sql.py --db ztb_clean --sql "你的 SQL"
```

确认用了索引（不能全表扫），否则加 FULLTEXT 或调整写法。

5. 加测试 `test/test_xxx_query.py`

---

## 八、排查与急救

### 8.1 常见 Git 错误

| 报错 | 原因 | 解决 |
|---|---|---|
| `Permission denied (publickey)` | SSH 没配好 | 重做 §1.1 |
| `src refspec xxx does not match any` | 没 commit | `git add` + `git commit` |
| `non-fast-forward` | 远端有你没拉到的 commit | `git pull --rebase origin 分支名` |
| `CONFLICT (content): Merge conflict` | 合并冲突 | 见 §8.2 |
| `failed to push some refs` | 同上 | 同上 |

### 8.2 合并冲突

```bash
# 1. 拉最新 main 到自己分支
git checkout feat/xxx
git fetch origin
git rebase origin/main          # 或 git merge origin/main

# 2. 终端提示冲突文件
# CONFLICT (content): Merge conflict in agent/router.py

# 3. 打开冲突文件，找这种标记：
# <<<<<<<< HEAD
# 你的代码
# ========
# 别人的代码
# >>>>>>>> origin/main
# 手动编辑成你想要的样子，删 <<<<<<< ======= >>>>>>> 三行

# 4. 标记冲突已解决
git add agent/router.py
git rebase --continue            # 或 git commit（merge 模式）

# 5. 推送
git push --force-with-lease origin feat/xxx
# `--force-with-lease` 比 `--force` 安全：拒绝覆盖别人刚 push 的内容
```

### 8.3 撤销 / 回退

| 场景 | 命令 | 说明 |
|---|---|---|
| 撤销未暂存的改动 | `git checkout -- xxx.py` | ⚠️ 改动会丢 |
| 撤销已暂存 | `git restore --staged xxx.py` | 文件不变，只是取消暂存 |
| 改 commit message | `git commit --amend -m "新信息"` | 仅最后一次 commit |
| 撤销最后一次 commit（保留改动） | `git reset --soft HEAD~1` | 改动回到工作区 |
| 撤销最后一次 commit（删除改动） | `git reset --hard HEAD~1` | ⚠️ 改动会丢 |
| 撤回已 push 的 commit | `git revert HEAD` + `git push` | 生成反向 commit，保留历史 |

### 8.4 🚨 紧急：.env 不小心 push 到远端

**立即行动**（按顺序）：

1. **轮换所有 key**（假设已泄露）：
 - DeepSeek: https://platform.deepseek.com → API Keys → 删除旧 key → 创建新
 - SiliconFlow / MinerU / Tavily 同上
 - MySQL 密码：在数据库改密码 + 更新 `.env`
2. **通知项目 owner**（zonggao7-art）
3. **从 Git 历史中清除**：

```bash
# 用 git-filter-repo（推荐）或 BFG
pip install git-filter-repo
git filter-repo --in-place --path .env --path .env.example --invert-paths
git push origin main --force
```

4. **让所有 collaborator 重新 clone**（旧 clone 仍带泄露历史）

**预防**：
- 别 `git add -f .env`（强制添加）
- 别 `git add *`（通配符可能匹到 .env）
- 提交前 `git status` 检查

### 8.5 我改了文件但 git status 显示 clean

可能原因：

- 文件被 `.gitignore` 排除了（正常，比如 `.env`）
- 没保存（编辑器问题）
- 在另一个分支上

```bash
git status --ignored     # 显示被 ignore 的文件
git branch               # 当前分支
```

---

## 九、Code Review 守则

### 作为作者

- PR 描述写清楚"改了什么、为什么、怎么测"
- 单个 PR 不超过 400 行 diff（大了拆）
- 自测通过再请求 review
- review 反馈及时回复（24 小时内）
- 不同意 reviewer 意见时，技术论据说话，不情绪化

### 作为 Reviewer

- 24 小时内给首次反馈
- 评论分三类：
 - 🔴 **must fix**：阻塞合并
 - 🟡 **suggest**：建议改，不阻塞
 - 🟢 **nit**：可忽略，缩写 = "nitpick"
- 关注"对不对"，其次是"美不美"
- 不熟悉的代码先问，再评

---

## 十、写在最后

1. **别怕提 PR**：分支保护 + Squash merge = 任何错误都可回退，唯一的限制是你提交前自测
2. **不会就问**：卡住比闷头干更省时间——@owner 或群里发问
3. **小步快跑**：每天多个小 commit > 每周一个大 commit
4. **看 [CLAUDE.md](CLAUDE.md)**：架构、约束、命令都在那
5. **看 [README.md](README.md)**：项目介绍、快速开始

---

**有问题？** 在 GitHub 上提 Issue 或直接 @zonggao7-art。