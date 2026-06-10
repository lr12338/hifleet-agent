"""
Hifleet FAQ知识库导入脚本
专门处理FAQ文档，支持智能分块和分类导入
"""
import os
import re
import logging
from typing import List, Dict, Tuple
from pathlib import Path
from coze_coding_dev_sdk import KnowledgeClient, Config, KnowledgeDocument, DataSourceType, ChunkConfig
from coze_coding_utils.runtime_ctx.context import new_context

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FAQImporter:
    """FAQ知识库导入器"""
    
    def __init__(self, table_name: str = "coze_doc_knowledge"):
        """
        初始化导入器
        
        Args:
            table_name: 知识库表名
        """
        self.ctx = new_context(method="import_faq")
        self.config = Config()
        self.client = KnowledgeClient(config=self.config, ctx=self.ctx)
        self.table_name = table_name
        self.imported_count = 0
        
    def parse_qa_format(self, content: str) -> List[Dict]:
        """
        解析问答格式
        
        Args:
            content: 文档内容
        
        Returns:
            问答对列表
        """
        qa_list = []
        
        # 匹配格式：### Q1 问题 \n **问题**：xxx \n **解答**：xxx
        pattern = r'###\s+([Q\d]+|业务\d+)\s+(.+?)\n\*\*问题\*\*[：:]\s*(.+?)\n\*\*解答\*\*[：:]\s*(.+?)(?=\n>|\n###|\Z)'
        matches = re.finditer(pattern, content, re.DOTALL)
        
        for match in matches:
            qa_id = match.group(1).strip()
            title = match.group(2).strip()
            question = match.group(3).strip()
            answer = match.group(4).strip()
            
            # 提取分类标签
            tag_match = re.search(r'function:\s*([^\]]+)', match.group(0))
            category = tag_match.group(1).strip() if tag_match else "未分类"
            
            qa_list.append({
                "id": qa_id,
                "title": title,
                "question": question,
                "answer": answer,
                "category": category
            })
        
        logger.info(f"解析到 {len(qa_list)} 个问答对")
        return qa_list
    
    def parse_operation_guide(self, content: str) -> List[Dict]:
        """
        解析操作指南格式
        
        Args:
            content: 文档内容
        
        Returns:
            操作指南列表
        """
        guide_list = []
        
        # 匹配格式：## 1.1 标题 \n 内容
        pattern = r'##\s+([\d.]+)\s+(.+?)\n(.+?)(?=\n##|\n>|\Z)'
        matches = re.finditer(pattern, content, re.DOTALL)
        
        for match in matches:
            section_id = match.group(1).strip()
            title = match.group(2).strip()
            content_text = match.group(3).strip()
            
            # 提取分类标签
            tag_match = re.search(r'function:\s*([^\]]+)', match.group(0))
            category = tag_match.group(1).strip() if tag_match else "操作指南"
            
            guide_list.append({
                "id": section_id,
                "title": title,
                "content": content_text,
                "category": category
            })
        
        logger.info(f"解析到 {len(guide_list)} 个操作指南条目")
        return guide_list
    
    def import_qa_pairs(self, qa_list: List[Dict]) -> Dict:
        """
        导入问答对到知识库
        
        Args:
            qa_list: 问答对列表
        
        Returns:
            导入结果
        """
        try:
            documents = []
            
            for qa in qa_list:
                # 构建文档内容
                doc_content = f"【{qa['category']}】{qa['title']}\n\n"
                doc_content += f"问题：{qa['question']}\n\n"
                doc_content += f"答案：{qa['answer']}"
                
                documents.append(KnowledgeDocument(
                    source=DataSourceType.TEXT,
                    raw_data=doc_content
                ))
            
            # 批量导入
            response = self.client.add_documents(
                documents=documents,
                table_name=self.table_name,
                chunk_config=ChunkConfig(
                    separator="\n\n",
                    max_tokens=800,  # FAQ适合较小的分块
                    remove_extra_spaces=True
                )
            )
            
            if response.code == 0:
                self.imported_count += len(response.doc_ids)
                logger.info(f"✅ 成功导入 {len(response.doc_ids)} 个问答对")
                return {"success": True, "count": len(response.doc_ids)}
            else:
                logger.error(f"❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"导入异常: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_guide_items(self, guide_list: List[Dict]) -> Dict:
        """
        导入操作指南到知识库
        
        Args:
            guide_list: 操作指南列表
        
        Returns:
            导入结果
        """
        try:
            documents = []
            
            for guide in guide_list:
                # 构建文档内容
                doc_content = f"【{guide['category']}】{guide['id']} {guide['title']}\n\n"
                doc_content += guide['content']
                
                documents.append(KnowledgeDocument(
                    source=DataSourceType.TEXT,
                    raw_data=doc_content
                ))
            
            # 批量导入
            response = self.client.add_documents(
                documents=documents,
                table_name=self.table_name,
                chunk_config=ChunkConfig(
                    separator="\n\n",
                    max_tokens=1200,  # 操作指南适合中等分块
                    remove_extra_spaces=True
                )
            )
            
            if response.code == 0:
                self.imported_count += len(response.doc_ids)
                logger.info(f"✅ 成功导入 {len(response.doc_ids)} 个操作指南")
                return {"success": True, "count": len(response.doc_ids)}
            else:
                logger.error(f"❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"导入异常: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_faq_file(self, file_path: str) -> Dict:
        """
        导入FAQ文件
        
        Args:
            file_path: 文件路径
        
        Returns:
            导入结果
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            logger.info(f"📄 处理文件: {os.path.basename(file_path)}")
            
            # 判断文件类型并选择解析方式
            if "问答" in file_path or "Q" in content[:500]:
                qa_list = self.parse_qa_format(content)
                if qa_list:
                    return self.import_qa_pairs(qa_list)
            elif "操作指南" in file_path or "手册" in file_path:
                guide_list = self.parse_operation_guide(content)
                if guide_list:
                    return self.import_guide_items(guide_list)
            
            # 如果都不匹配，作为普通文档导入
            logger.info("使用普通文档导入方式...")
            return self.import_as_regular_doc(content, file_path)
            
        except Exception as e:
            logger.error(f"导入文件失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_as_regular_doc(self, content: str, file_path: str) -> Dict:
        """
        作为普通文档导入
        
        Args:
            content: 文档内容
            file_path: 文件路径
        
        Returns:
            导入结果
        """
        try:
            document = KnowledgeDocument(
                source=DataSourceType.TEXT,
                raw_data=content
            )
            
            response = self.client.add_documents(
                documents=[document],
                table_name=self.table_name,
                chunk_config=ChunkConfig(
                    separator="\n\n",
                    max_tokens=1500,
                    remove_extra_spaces=True
                )
            )
            
            if response.code == 0:
                self.imported_count += len(response.doc_ids)
                logger.info(f"✅ 成功导入文档: {len(response.doc_ids)} 个块")
                return {"success": True, "count": len(response.doc_ids)}
            else:
                logger.error(f"❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"导入异常: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_all_faq(self, faq_dir: str = "docs/FAQ") -> Dict:
        """
        导入所有FAQ文档
        
        Args:
            faq_dir: FAQ目录路径
        
        Returns:
            导入结果统计
        """
        logger.info("=" * 60)
        logger.info("开始导入FAQ知识库")
        logger.info("=" * 60)
        
        faq_path = Path(faq_dir)
        
        if not faq_path.exists():
            logger.error(f"FAQ目录不存在: {faq_dir}")
            return {"success": False, "error": "目录不存在"}
        
        results = {
            "total_files": 0,
            "success_files": 0,
            "total_items": 0,
            "details": []
        }
        
        # 遍历所有Markdown文件
        for file_path in faq_path.glob("*.md"):
            results["total_files"] += 1
            logger.info(f"\n处理文件: {file_path.name}")
            
            result = self.import_faq_file(str(file_path))
            
            if result.get("success"):
                results["success_files"] += 1
                results["total_items"] += result.get("count", 0)
                results["details"].append({
                    "file": file_path.name,
                    "status": "success",
                    "count": result.get("count", 0)
                })
            else:
                results["details"].append({
                    "file": file_path.name,
                    "status": "failed",
                    "error": result.get("error", "未知错误")
                })
        
        # 输出总结
        logger.info("\n" + "=" * 60)
        logger.info("导入完成！")
        logger.info("=" * 60)
        logger.info(f"总文件数: {results['total_files']}")
        logger.info(f"成功文件数: {results['success_files']}")
        logger.info(f"总导入条目: {results['total_items']}")
        logger.info(f"累计导入条目: {self.imported_count}")
        
        return results


def main():
    """主函数"""
    importer = FAQImporter()
    
    # 导入所有FAQ
    results = importer.import_all_faq("docs/FAQ")
    
    # 测试检索
    print("\n" + "=" * 60)
    print("测试FAQ检索效果")
    print("=" * 60)
    
    test_queries = [
        "我是免费用户，为什么看不到最新船位？",
        "如何修改密码？",
        "岸基AIS和卫星AIS有什么区别？",
        "如何创建船队？",
        "怎样查询船舶历史轨迹？"
    ]
    
    for query in test_queries:
        print(f"\n🔍 查询: {query}")
        
        response = importer.client.search(
            query=query,
            top_k=3,
            min_score=0.5
        )
        
        if response.code == 0 and response.chunks:
            print(f"✅ 找到 {len(response.chunks)} 个结果")
            print(f"   最高相似度: {response.chunks[0].score:.2%}")
            print(f"   内容预览: {response.chunks[0].content[:100]}...")
        else:
            print("❌ 未找到相关结果")
    
    print("\n" + "=" * 60)
    print("FAQ导入完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
