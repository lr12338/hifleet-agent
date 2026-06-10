# Hifleet知识库管理与使用指南

## 目录

1. [概述](#概述)
2. [向量知识库原理](#向量知识库原理)
3. [知识库导入策略](#知识库导入策略)
4. [使用方法](#使用方法)
5. [最佳实践](#最佳实践)
6. [常见问题](#常见问题)

---

## 概述

Hifleet智能客服的知识库基于**向量检索（RAG）**技术，支持语义搜索而非简单的关键词匹配。这意味着：

- ✅ 用户可以用自然语言提问，无需精确匹配
- ✅ 系统能理解问题的语义，返回最相关的答案
- ✅ 支持多种文档类型（产品文档、FAQ、API文档等）

### 已导入的知识内容

#### 1. 产品文档（4份）
- `01-项目背景与问题定义.md` - 项目背景和目标
- `02-用户场景与业务流程.md` - 用户场景分析
- `03-产品能力结构与方案设计.md` - 产品架构设计
- `04-技术深挖与产品判断.md` - 技术方案说明

#### 2. FAQ知识（4类）
- **账号注册FAQ**：注册流程、密码重置等
- **产品功能FAQ**：版本差异、升级方式等
- **船位查询FAQ**：查询方法、数据更新等
- **API使用FAQ**：密钥获取、调用限制等

#### 3. 操作指南（2份）
- **船队管理指南**：添加船舶、标签管理等
- **气象查询指南**：气象数据查询方法

---

## 向量知识库原理

### 什么是向量检索？

```
用户问题 → 向量化 → 在向量空间中搜索 → 返回最相似的文档块
```

### 核心概念

1. **Embedding（嵌入）**
   - 将文本转换为高维向量
   - 语义相似的文本在向量空间中距离更近
   - 支持跨语言、跨表达方式的检索

2. **相似度计算**
   - 使用余弦相似度（Cosine Similarity）
   - 范围：0.0 ~ 1.0
   - 值越高表示越相似

3. **分块（Chunking）**
   - 将长文档切分成小块
   - 便于检索和管理
   - 避免返回过多无关内容

### 检索流程

```mermaid
graph LR
    A[用户提问] --> B[问题向量化]
    B --> C[向量搜索]
    C --> D[相似度排序]
    D --> E[过滤低置信度]
    E --> F[返回Top-K结果]
```

---

## 知识库导入策略

### 智能分块策略

根据文档类型采用不同的分块策略：

| 文档类型 | 分块方式 | 最大Token数 | 说明 |
|---------|---------|-----------|------|
| **product_doc** | 按章节 | 2000 | 保留完整上下文 |
| **faq** | 按问答对 | 500 | 精确匹配问答 |
| **api_doc** | 按接口 | 1500 | 保留接口完整性 |
| **user_guide** | 按段落 | 1000 | 操作步骤完整 |

### 分块示例

#### FAQ分块
```
原文：
Q: 如何注册账号？
A: 访问官网，点击注册...

Q: 忘记密码怎么办？
A: 点击忘记密码链接...

分块后：
块1: 问题：如何注册账号？\n答案：访问官网...
块2: 问题：忘记密码怎么办？\n答案：点击忘记密码...
```

#### 产品文档分块
```
原文：
# 产品功能介绍
## 基础功能
...

## 专业版功能
...

分块后：
块1: # 产品功能介绍\n\n## 基础功能\n...
块2: # 产品功能介绍\n\n## 专业版功能\n...
```

---

## 使用方法

### 方法一：使用CLI工具

#### 1. 批量导入知识库
```bash
python scripts/knowledge_base_cli.py batch-import
```

#### 2. 导入单个文件
```bash
python scripts/knowledge_base_cli.py import-doc --file assets/new_doc.md --type product_doc
```

#### 3. 导入文本内容
```bash
python scripts/knowledge_base_cli.py import-doc \
  --text "如何申请退款？购买后7天内可申请全额退款。" \
  --title "退款政策" \
  --type faq
```

#### 4. 导入网页URL
```bash
python scripts/knowledge_base_cli.py import-doc \
  --url "https://www.hifleet.com/docs/api" \
  --type api_doc
```

#### 5. 检索知识库
```bash
python scripts/knowledge_base_cli.py search "如何注册账号" --top-k 3 --min-score 0.6
```

#### 6. 测试检索效果
```bash
python scripts/knowledge_base_cli.py test-search
```

### 方法二：使用Python SDK

#### 导入文档
```python
from scripts.enhanced_import_knowledge import HifleetKnowledgeImporter

# 初始化导入器
importer = HifleetKnowledgeImporter(table_name="coze_doc_knowledge")

# 导入文件
result = importer.import_from_file("assets/new_doc.md", doc_type="product_doc")

# 导入文本
result = importer.import_text("问题答案内容", "FAQ标题", doc_type="faq")

# 导入URL
result = importer.import_from_url("https://example.com/doc", doc_type="web_page")

# 测试检索
result = importer.test_search("如何注册账号", top_k=3, min_score=0.6)
```

#### 直接检索
```python
from coze_coding_dev_sdk import KnowledgeClient, Config

client = KnowledgeClient(config=Config())
response = client.search(
    query="如何注册账号",
    top_k=5,
    min_score=0.6
)

for chunk in response.chunks:
    print(f"相关度: {chunk.score}")
    print(f"内容: {chunk.content}")
```

### 方法三：在Agent中使用

知识库检索已集成到Agent工具中，Agent会自动调用：

```python
from agents.agent import build_agent

agent = build_agent()
result = agent.invoke({"messages": [{"role": "user", "content": "如何注册账号？"}]})
```

---

## 最佳实践

### 1. 文档准备

#### ✅ 好的文档
```markdown
# 如何注册Hifleet账号

## 注册步骤
1. 访问官网 www.hifleet.com
2. 点击右上角"注册"按钮
3. 填写邮箱和密码
4. 验证邮箱

## 注意事项
- 密码至少8位，包含字母和数字
- 验证邮件有效期24小时
```

#### ❌ 不好的文档
```
注册就在官网点注册就行了
```

### 2. 分块大小选择

| 场景 | 推荐Token数 | 原因 |
|------|-----------|------|
| FAQ问答 | 300-500 | 问题答案完整，快速匹配 |
| 操作指南 | 800-1200 | 操作步骤完整，便于理解 |
| 产品文档 | 1500-2000 | 保留上下文，语义完整 |
| API文档 | 1000-1500 | 接口说明完整 |

### 3. 检索参数调优

#### min_score（最小相似度阈值）
- **0.7-0.8**：高精度，返回最相关结果（适合FAQ）
- **0.5-0.6**：平衡，适合大多数场景（推荐）
- **0.3-0.4**：宽松，可能返回不相关结果

#### top_k（返回结果数）
- **3-5**：推荐，平衡效果和性能
- **1-2**：高精度场景
- **5-10**：需要更多上下文时

### 4. 知识库维护

#### 定期更新
```bash
# 每月检查知识库效果
python scripts/knowledge_base_cli.py test-search

# 更新文档后重新导入
python scripts/knowledge_base_cli.py import-doc --file new_doc.md
```

#### 监控指标
- 检索成功率（找到相关结果的比率）
- 平均相似度（相关结果的平均得分）
- 用户反馈（答案是否有帮助）

---

## 常见问题

### Q1: 为什么检索不到结果？

**可能原因：**
1. 文档未导入或导入失败
2. min_score设置过高
3. 问题表述与文档差异太大
4. 知识库内容不足

**解决方案：**
```bash
# 1. 降低相似度阈值
python scripts/knowledge_base_cli.py search "问题" --min-score 0.4

# 2. 检查是否导入成功
python scripts/knowledge_base_cli.py batch-import

# 3. 补充相关知识
python scripts/knowledge_base_cli.py import-doc --text "补充内容" --title "标题"
```

### Q2: 检索结果不相关？

**可能原因：**
1. min_score设置过低
2. 文档内容质量问题
3. 分块不合理

**解决方案：**
```bash
# 1. 提高相似度阈值
python scripts/knowledge_base_cli.py search "问题" --min-score 0.7

# 2. 优化文档内容（更具体、更结构化）

# 3. 调整分块策略（修改import脚本中的chunk_config）
```

### Q3: 如何导入大量文档？

**批量导入脚本：**
```python
import os
from scripts.enhanced_import_knowledge import HifleetKnowledgeImporter

importer = HifleetKnowledgeImporter()

# 遍历目录
for root, dirs, files in os.walk("docs/"):
    for file in files:
        if file.endswith('.md'):
            filepath = os.path.join(root, file)
            importer.import_from_file(filepath, doc_type="product_doc")
```

### Q4: 如何删除或更新文档？

当前SDK暂不支持直接删除，解决方案：
1. 创建新的数据集（table_name）
2. 重新导入更新后的文档

### Q5: 支持哪些文档格式？

| 格式 | 支持方式 |
|------|---------|
| Markdown (.md) | 文本导入 |
| 纯文本 (.txt) | 文本导入 |
| JSON (.json) | 文本导入 |
| 网页 (URL) | URL导入 |
| 对象存储 (URI) | URI导入 |

### Q6: 如何评估知识库效果？

```bash
# 运行测试检索
python scripts/knowledge_base_cli.py test-search

# 查看输出
# ✅ 找到 3 个结果
#    最高相似度: 97.32%  ← 高于80%表示效果好
```

---

## 技术架构

### 向量数据库

- **类型**：分布式向量数据库
- **索引**：HNSW（Hierarchical Navigable Small World）
- **维度**：1024维（默认）
- **度量**：余弦相似度

### 检索性能

- **响应时间**：<100ms
- **吞吐量**：支持高并发
- **容量**：支持百万级文档

---

## 相关文件

| 文件路径 | 说明 |
|---------|------|
| `scripts/enhanced_import_knowledge.py` | 增强版导入脚本 |
| `scripts/knowledge_base_cli.py` | CLI管理工具 |
| `src/tools/knowledge_search_tool.py` | 基础检索工具 |
| `src/tools/enhanced_knowledge_search_tool.py` | 增强检索工具 |
| `scripts/import_knowledge.py` | 基础导入脚本 |

---

## 总结

Hifleet知识库采用先进的向量检索技术，能够：

✅ **智能检索**：理解用户问题的语义，返回最相关的答案  
✅ **多种导入**：支持文件、文本、URL、对象存储  
✅ **灵活配置**：可调整分块策略、相似度阈值等  
✅ **易于维护**：提供CLI工具和SDK接口  

通过合理配置和持续优化，知识库能够显著提升智能客服的问答准确率和用户满意度。

---

**技术支持**
如有问题，请参考：
- CLI工具：`python scripts/knowledge_base_cli.py --help`
- SDK文档：`/skills/public/prod/knowledge/references/python/README.md`
