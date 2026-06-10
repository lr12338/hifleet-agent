#!/usr/bin/env python3
"""
RAG知识库导入脚本
将docs/RAG目录下的知识文档导入到向量数据库

数据集规划：
- hifleet_cs_outputs: FAQ检索词、客服问答对、标准回复话术（优先级最高）
- hifleet_cs_wiki: 产品功能、平台操作、常见问题主题页（补充）

使用方式：
    python scripts/import_rag_knowledge.py
    python scripts/import_rag_knowledge.py --test  # 仅测试检索效果
"""
import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from coze_coding_dev_sdk import KnowledgeClient, Config, KnowledgeDocument, DataSourceType, ChunkConfig
from coze_coding_utils.runtime_ctx.context import new_context

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 知识库数据集配置
# 使用带版本号的数据集名称，避免旧数据干扰
OUTPUTS_DATASET = "hifleet_cs_outputs_v2"  # FAQ和标准回复（优先级最高）
WIKI_DATASET = "hifleet_cs_wiki_v2"        # 主题页和背景知识（补充）

# RAG文档目录
RAG_DIR = Path(project_root) / "docs" / "RAG"
OUTPUTS_DIR = RAG_DIR / "hifleet_cs_outputs"
WIKI_DIR = RAG_DIR / "hifleet_cs_wiki"


class RAGKnowledgeImporter:
    """RAG知识库导入器"""
    
    def __init__(self):
        """初始化导入器"""
        self.ctx = new_context(method="import_rag_knowledge")
        self.config = Config()
        self.client = KnowledgeClient(config=self.config, ctx=self.ctx)
        self.stats = {
            "outputs": {"total": 0, "success": 0, "failed": 0},
            "wiki": {"total": 0, "success": 0, "failed": 0}
        }
    
    def import_all(self) -> Dict[str, Any]:
        """
        导入所有RAG知识库
        
        Returns:
            导入统计信息
        """
        logger.info("=" * 70)
        logger.info("开始导入RAG知识库")
        logger.info("=" * 70)
        
        # 1. 导入outputs数据集（FAQ和标准回复）
        logger.info("\n【第1步】导入FAQ和标准回复（hifleet_cs_outputs）")
        logger.info("-" * 70)
        self._import_outputs()
        
        # 2. 导入wiki数据集（主题页）
        logger.info("\n【第2步】导入主题页（hifleet_cs_wiki）")
        logger.info("-" * 70)
        self._import_wiki()
        
        # 3. 打印统计信息
        self._print_stats()
        
        return self.stats
    
    def _import_outputs(self):
        """导入outputs数据集"""
        if not OUTPUTS_DIR.exists():
            logger.error(f"outputs目录不存在: {OUTPUTS_DIR}")
            return
        
        for file_path in OUTPUTS_DIR.glob("*.md"):
            if file_path.name == "INDEX.md":
                continue  # 跳过索引文件
            
            self.stats["outputs"]["total"] += 1
            logger.info(f"\n📄 处理文件: {file_path.name}")
            
            result = self._import_file(file_path, OUTPUTS_DATASET, doc_type="outputs")
            
            if result["success"]:
                self.stats["outputs"]["success"] += 1
            else:
                self.stats["outputs"]["failed"] += 1
        
        # 导入JSONL文件
        jsonl_file = OUTPUTS_DIR / "客服知识库结构化.jsonl"
        if jsonl_file.exists():
            self.stats["outputs"]["total"] += 1
            logger.info(f"\n📄 处理文件: {jsonl_file.name}")
            result = self._import_jsonl(jsonl_file, OUTPUTS_DATASET)
            if result["success"]:
                self.stats["outputs"]["success"] += 1
            else:
                self.stats["outputs"]["failed"] += 1
    
    def _import_wiki(self):
        """导入wiki数据集"""
        if not WIKI_DIR.exists():
            logger.error(f"wiki目录不存在: {WIKI_DIR}")
            return
        
        for file_path in WIKI_DIR.glob("*.md"):
            if file_path.name == "INDEX.md":
                continue  # 跳过索引文件
            
            self.stats["wiki"]["total"] += 1
            logger.info(f"\n📄 处理文件: {file_path.name}")
            
            result = self._import_file(file_path, WIKI_DATASET, doc_type="wiki")
            
            if result["success"]:
                self.stats["wiki"]["success"] += 1
            else:
                self.stats["wiki"]["failed"] += 1
    
    def _import_file(self, file_path: Path, table_name: str, doc_type: str = "outputs") -> Dict:
        """
        导入单个文件
        
        Args:
            file_path: 文件路径
            table_name: 目标数据集名称
            doc_type: 文档类型
            
        Returns:
            导入结果
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 根据文档类型选择分块策略
            # 【重要】使用特殊分隔符避免内容被错误切分
            if doc_type == "outputs":
                # FAQ和标准回复：每个条目作为完整单元
                chunk_config = ChunkConfig(
                    separator="|||CHUNK|||",  # 不会在内容中出现的分隔符
                    max_tokens=1500,  # 增大chunk大小
                    remove_extra_spaces=True
                )
            else:
                # wiki主题页：按大段落分块
                chunk_config = ChunkConfig(
                    separator="\n## ",  # 按Markdown标题分块
                    max_tokens=2000,
                    remove_extra_spaces=True
                )
            
            # 添加来源标记
            source_tag = f"\n\n【来源：{file_path.stem}】"
            content_with_tag = content + source_tag
            
            document = KnowledgeDocument(
                source=DataSourceType.TEXT,
                raw_data=content_with_tag
            )
            
            response = self.client.add_documents(
                documents=[document],
                table_name=table_name,
                chunk_config=chunk_config
            )
            
            if response.code == 0:
                logger.info(f"   ✅ 成功导入 {len(response.doc_ids)} 个文档块")
                return {"success": True, "count": len(response.doc_ids)}
            else:
                logger.error(f"   ❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"   ❌ 导入异常: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _import_jsonl(self, file_path: Path, table_name: str) -> Dict:
        """
        导入JSONL文件
        
        Args:
            file_path: JSONL文件路径
            table_name: 目标数据集名称
            
        Returns:
            导入结果
        """
        try:
            documents = []
            
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    item = json.loads(line)
                    
                    # 构建文档内容
                    content = self._format_jsonl_item(item)
                    content += f"\n\n【来源：{file_path.stem}】"
                    
                    documents.append(KnowledgeDocument(
                        source=DataSourceType.TEXT,
                        raw_data=content
                    ))
            
            if not documents:
                logger.warning("   ⚠️ JSONL文件为空")
                return {"success": True, "count": 0}
            
            response = self.client.add_documents(
                documents=documents,
                table_name=table_name,
                chunk_config=ChunkConfig(
                    separator="|||CHUNK|||",  # 使用不会在内容中出现的分隔符，避免切分
                    max_tokens=2000,  # 增大chunk大小，确保每个QA条目完整
                    remove_extra_spaces=True
                )
            )
            
            if response.code == 0:
                logger.info(f"   ✅ 成功导入 {len(response.doc_ids)} 个JSONL条目")
                return {"success": True, "count": len(response.doc_ids)}
            else:
                logger.error(f"   ❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"   ❌ JSONL导入异常: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _format_jsonl_item(self, item: Dict) -> str:
        """
        格式化JSONL条目为文档内容
        
        【重要】使用特殊分隔符避免被切分
        每个QA条目作为一个完整的语义单元，不应被分块切分
        """
        parts = []
        
        # 构建结构化内容，使用单行格式避免被\n\n切分
        if item.get("keywords"):
            parts.append(f"【关键词】{', '.join(item['keywords'])}")
        
        if item.get("question"):
            parts.append(f"【问题】{item['question']}")
        
        if item.get("answer"):
            parts.append(f"【答案】{item['answer']}")
        
        if item.get("related_topics"):
            parts.append(f"【相关主题】{', '.join(item['related_topics'])}")
        
        if item.get("category"):
            parts.append(f"【分类】{item['category']}")
        
        if item.get("escalate_when"):
            escalate_text = '；'.join(item['escalate_when'])
            parts.append(f"【转人工场景】{escalate_text}")
        
        # 使用换行符连接，不使用双换行（避免被切分）
        return "\n".join(parts)
    
    def _print_stats(self):
        """打印导入统计"""
        logger.info("\n" + "=" * 70)
        logger.info("导入统计")
        logger.info("=" * 70)
        
        logger.info("\n【hifleet_cs_outputs - FAQ/标准回复】")
        logger.info(f"  总文件数: {self.stats['outputs']['total']}")
        logger.info(f"  成功: {self.stats['outputs']['success']}")
        logger.info(f"  失败: {self.stats['outputs']['failed']}")
        
        logger.info("\n【hifleet_cs_wiki - 主题页】")
        logger.info(f"  总文件数: {self.stats['wiki']['total']}")
        logger.info(f"  成功: {self.stats['wiki']['success']}")
        logger.info(f"  失败: {self.stats['wiki']['failed']}")
        
        total_success = self.stats['outputs']['success'] + self.stats['wiki']['success']
        total_files = self.stats['outputs']['total'] + self.stats['wiki']['total']
        
        logger.info(f"\n总计: {total_success}/{total_files} 文件导入成功")
        logger.info("=" * 70)
    
    def test_search(self):
        """测试检索效果"""
        logger.info("\n" + "=" * 70)
        logger.info("测试知识库检索效果")
        logger.info("=" * 70)
        
        test_queries = [
            # outputs测试（FAQ和标准回复）
            ("怎么注册账号？", OUTPUTS_DATASET),
            ("DTU是什么？", OUTPUTS_DATASET),
            ("忘记密码怎么办？", OUTPUTS_DATASET),
            ("会员价格是多少？", OUTPUTS_DATASET),
            # wiki测试（主题页）
            ("航线优化是什么？", WIKI_DATASET),
            ("如何查看历史轨迹？", WIKI_DATASET),
            ("AIS更新频率是多少？", WIKI_DATASET),
        ]
        
        for query, dataset in test_queries:
            logger.info(f"\n🔍 查询: {query}")
            logger.info(f"   数据集: {dataset}")
            
            try:
                response = self.client.search(
                    query=query,
                    table_names=[dataset],
                    top_k=3,
                    min_score=0.5
                )
                
                if response.code == 0 and response.chunks:
                    logger.info(f"   ✅ 找到 {len(response.chunks)} 个结果")
                    for i, chunk in enumerate(response.chunks[:2], 1):
                        logger.info(f"   结果{i}: 相关度={chunk.score:.2f}")
                        logger.info(f"         预览: {chunk.content[:80]}...")
                else:
                    logger.info("   ❌ 未找到相关结果")
                    
            except Exception as e:
                logger.error(f"   ❌ 检索异常: {str(e)}")
        
        # 测试全库检索
        logger.info("\n" + "-" * 70)
        logger.info("测试全库检索（不指定数据集）")
        logger.info("-" * 70)
        
        for query in ["怎么注册账号", "DTU和通用DTU区别", "航线优化"]:
            logger.info(f"\n🔍 全库查询: {query}")
            
            try:
                response = self.client.search(
                    query=query,
                    top_k=5,
                    min_score=0.5
                )
                
                if response.code == 0 and response.chunks:
                    logger.info(f"   ✅ 找到 {len(response.chunks)} 个结果")
                    for chunk in response.chunks[:3]:
                        logger.info(f"   - 相关度={chunk.score:.2f}: {chunk.content[:60]}...")
                else:
                    logger.info("   ❌ 未找到相关结果")
                    
            except Exception as e:
                logger.error(f"   ❌ 检索异常: {str(e)}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="RAG知识库导入工具")
    parser.add_argument("--test", action="store_true", help="仅测试检索效果，不导入")
    args = parser.parse_args()
    
    importer = RAGKnowledgeImporter()
    
    if args.test:
        importer.test_search()
    else:
        importer.import_all()
        logger.info("\n" + "=" * 70)
        logger.info("导入完成，开始测试检索效果...")
        importer.test_search()


if __name__ == "__main__":
    main()
