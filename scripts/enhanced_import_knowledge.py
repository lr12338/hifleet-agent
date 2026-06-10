"""
Hifleet知识库增强导入脚本
支持多种数据源、智能分块、元数据管理
"""
import os
import json
import logging
from typing import List, Dict, Optional
from pathlib import Path
from coze_coding_dev_sdk import KnowledgeClient, Config, KnowledgeDocument, DataSourceType, ChunkConfig
from coze_coding_utils.runtime_ctx.context import new_context

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HifleetKnowledgeImporter:
    """Hifleet知识库导入器"""
    
    def __init__(self, table_name: str = "coze_doc_knowledge"):
        """
        初始化导入器
        
        Args:
            table_name: 知识库表名
        """
        self.ctx = new_context(method="import_knowledge")
        self.config = Config()
        self.client = KnowledgeClient(config=self.config, ctx=self.ctx)
        self.table_name = table_name
        self.imported_docs = []
        
    def import_from_file(self, file_path: str, doc_type: str = "product_doc") -> Dict:
        """
        从本地文件导入文档
        
        Args:
            file_path: 文件路径（支持.md, .txt, .json）
            doc_type: 文档类型（product_doc, faq, api_doc, user_guide等）
        
        Returns:
            导入结果
        """
        try:
            path = Path(file_path)
            
            if not path.exists():
                logger.error(f"文件不存在: {file_path}")
                return {"success": False, "error": "文件不存在"}
            
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            logger.info(f"读取文件: {path.name}, 大小: {len(content)} 字符")
            
            # 智能分块处理
            documents = self._smart_chunk(content, path.stem, doc_type)
            
            # 导入到知识库
            response = self.client.add_documents(
                documents=documents,
                table_name=self.table_name,
                chunk_config=self._get_chunk_config(doc_type)
            )
            
            if response.code == 0:
                logger.info(f"✅ 成功导入 {len(response.doc_ids)} 个文档块")
                self.imported_docs.extend(response.doc_ids)
                return {
                    "success": True,
                    "file": path.name,
                    "chunks": len(response.doc_ids),
                    "doc_ids": response.doc_ids
                }
            else:
                logger.error(f"❌ 导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"导入文件失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_from_url(self, url: str, doc_type: str = "web_page") -> Dict:
        """
        从URL导入文档
        
        Args:
            url: 网页URL
            doc_type: 文档类型
        
        Returns:
            导入结果
        """
        try:
            logger.info(f"从URL导入: {url}")
            
            document = KnowledgeDocument(
                source=DataSourceType.URL,
                url=url
            )
            
            response = self.client.add_documents(
                documents=[document],
                table_name=self.table_name,
                chunk_config=self._get_chunk_config(doc_type)
            )
            
            if response.code == 0:
                logger.info(f"✅ URL导入成功，文档ID: {response.doc_ids}")
                self.imported_docs.extend(response.doc_ids)
                return {"success": True, "url": url, "doc_ids": response.doc_ids}
            else:
                logger.error(f"❌ URL导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"URL导入失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_from_uri(self, uri: str, doc_type: str = "object_storage") -> Dict:
        """
        从对象存储URI导入文档
        
        Args:
            uri: 对象存储URI（如 s3://bucket/key）
            doc_type: 文档类型
        
        Returns:
            导入结果
        """
        try:
            logger.info(f"从URI导入: {uri}")
            
            document = KnowledgeDocument(
                source=DataSourceType.URI,
                uri=uri
            )
            
            response = self.client.add_documents(
                documents=[document],
                table_name=self.table_name,
                chunk_config=self._get_chunk_config(doc_type)
            )
            
            if response.code == 0:
                logger.info(f"✅ URI导入成功，文档ID: {response.doc_ids}")
                self.imported_docs.extend(response.doc_ids)
                return {"success": True, "uri": uri, "doc_ids": response.doc_ids}
            else:
                logger.error(f"❌ URI导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"URI导入失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def import_text(self, text: str, title: str, doc_type: str = "faq") -> Dict:
        """
        直接导入文本内容
        
        Args:
            text: 文本内容
            title: 文档标题
            doc_type: 文档类型
        
        Returns:
            导入结果
        """
        try:
            logger.info(f"导入文本: {title}")
            
            # 添加标题和元数据
            content = f"# {title}\n\n{text}"
            
            document = KnowledgeDocument(
                source=DataSourceType.TEXT,
                raw_data=content
            )
            
            response = self.client.add_documents(
                documents=[document],
                table_name=self.table_name,
                chunk_config=self._get_chunk_config(doc_type)
            )
            
            if response.code == 0:
                logger.info(f"✅ 文本导入成功，文档ID: {response.doc_ids}")
                self.imported_docs.extend(response.doc_ids)
                return {"success": True, "title": title, "doc_ids": response.doc_ids}
            else:
                logger.error(f"❌ 文本导入失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"文本导入失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _smart_chunk(self, content: str, title: str, doc_type: str) -> List[KnowledgeDocument]:
        """
        智能分块：根据文档类型和结构进行分块
        
        Args:
            content: 文档内容
            title: 文档标题
            doc_type: 文档类型
        
        Returns:
            文档块列表
        """
        documents = []
        
        # 对于FAQ类型，按问答对分块
        if doc_type == "faq":
            # 识别问答对（格式：Q: xxx\nA: xxx）
            qa_pairs = self._extract_qa_pairs(content)
            for i, (q, a) in enumerate(qa_pairs, 1):
                doc_content = f"问题：{q}\n\n答案：{a}"
                documents.append(KnowledgeDocument(
                    source=DataSourceType.TEXT,
                    raw_data=doc_content
                ))
        
        # 对于API文档，按接口分块
        elif doc_type == "api_doc":
            # 按接口标题分块
            sections = self._split_by_headers(content, level=2)
            for section_title, section_content in sections:
                doc_content = f"## {section_title}\n\n{section_content}"
                documents.append(KnowledgeDocument(
                    source=DataSourceType.TEXT,
                    raw_data=doc_content
                ))
        
        # 对于产品文档，按章节分块
        elif doc_type == "product_doc":
            # 按一级标题分块
            sections = self._split_by_headers(content, level=1)
            for section_title, section_content in sections:
                doc_content = f"# {section_title}\n\n{section_content}"
                documents.append(KnowledgeDocument(
                    source=DataSourceType.TEXT,
                    raw_data=doc_content
                ))
        
        # 默认：整体作为一个文档
        else:
            documents.append(KnowledgeDocument(
                source=DataSourceType.TEXT,
                raw_data=content
            ))
        
        logger.info(f"智能分块完成，生成 {len(documents)} 个文档块")
        return documents
    
    def _extract_qa_pairs(self, content: str) -> List[tuple]:
        """提取问答对"""
        qa_pairs = []
        lines = content.split('\n')
        current_q = None
        current_a = []
        
        for line in lines:
            if line.startswith('Q:') or line.startswith('问：'):
                if current_q and current_a:
                    qa_pairs.append((current_q, '\n'.join(current_a)))
                current_q = line[2:].strip() if line.startswith('Q:') else line[2:].strip()
                current_a = []
            elif line.startswith('A:') or line.startswith('答：'):
                current_a.append(line[2:].strip() if line.startswith('A:') else line[2:].strip())
            elif current_a:
                current_a.append(line)
        
        if current_q and current_a:
            qa_pairs.append((current_q, '\n'.join(current_a)))
        
        return qa_pairs
    
    def _split_by_headers(self, content: str, level: int = 1) -> List[tuple]:
        """按标题分块"""
        sections = []
        lines = content.split('\n')
        current_title = "概述"
        current_content = []
        header_prefix = '#' * level
        
        for line in lines:
            if line.startswith(header_prefix + ' '):
                if current_content:
                    sections.append((current_title, '\n'.join(current_content)))
                current_title = line[level+1:].strip()
                current_content = []
            else:
                current_content.append(line)
        
        if current_content:
            sections.append((current_title, '\n'.join(current_content)))
        
        return sections
    
    def _get_chunk_config(self, doc_type: str) -> ChunkConfig:
        """
        根据文档类型获取分块配置
        
        Args:
            doc_type: 文档类型
        
        Returns:
            分块配置
        """
        configs = {
            # FAQ：较小的分块，精确匹配
            "faq": ChunkConfig(
                separator="\n\n",
                max_tokens=500,
                remove_extra_spaces=True
            ),
            # API文档：中等分块，保留接口完整性
            "api_doc": ChunkConfig(
                separator="\n\n",
                max_tokens=1500,
                remove_extra_spaces=True
            ),
            # 产品文档：较大分块，保留上下文
            "product_doc": ChunkConfig(
                separator="\n\n\n",
                max_tokens=2000,
                remove_extra_spaces=True
            ),
            # 用户指南：中等分块
            "user_guide": ChunkConfig(
                separator="\n\n",
                max_tokens=1000,
                remove_extra_spaces=True
            ),
            # 网页内容：默认配置
            "web_page": ChunkConfig(
                separator="\n\n",
                max_tokens=1500,
                remove_extra_spaces=True
            ),
            # 默认配置
            "default": ChunkConfig(
                separator="\n",
                max_tokens=1000,
                remove_extra_spaces=False
            )
        }
        
        return configs.get(doc_type, configs["default"])
    
    def test_search(self, query: str, top_k: int = 5, min_score: float = 0.6) -> Dict:
        """
        测试知识库检索效果
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            min_score: 最小相似度阈值
        
        Returns:
            检索结果
        """
        try:
            logger.info(f"测试检索: {query}")
            
            response = self.client.search(
                query=query,
                top_k=top_k,
                min_score=min_score
            )
            
            if response.code == 0:
                logger.info(f"✅ 检索成功，找到 {len(response.chunks)} 个结果")
                
                results = []
                for i, chunk in enumerate(response.chunks, 1):
                    results.append({
                        "rank": i,
                        "score": chunk.score,
                        "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                        "doc_id": chunk.doc_id
                    })
                    logger.info(f"  [{i}] 相似度: {chunk.score:.4f}")
                    logger.info(f"      内容: {chunk.content[:100]}...")
                
                return {"success": True, "query": query, "results": results}
            else:
                logger.error(f"❌ 检索失败: {response.msg}")
                return {"success": False, "error": response.msg}
                
        except Exception as e:
            logger.error(f"检索测试失败: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def get_import_summary(self) -> Dict:
        """获取导入总结"""
        return {
            "total_docs": len(self.imported_docs),
            "doc_ids": self.imported_docs,
            "table_name": self.table_name
        }


def main():
    """主函数：导入Hifleet知识库"""
    print("=" * 60)
    print("Hifleet知识库增强导入工具")
    print("=" * 60)
    
    # 初始化导入器
    importer = HifleetKnowledgeImporter(table_name="coze_doc_knowledge")
    
    # ===== 1. 导入本地产品文档 =====
    print("\n【步骤1】导入本地产品文档...")
    
    product_docs = [
        ("assets/01-项目背景与问题定义.md", "product_doc"),
        ("assets/02-用户场景与业务流程.md", "product_doc"),
        ("assets/03-产品能力结构与方案设计.md", "product_doc"),
        ("assets/04-技术深挖与产品判断.md", "product_doc"),
    ]
    
    for file_path, doc_type in product_docs:
        if os.path.exists(file_path):
            result = importer.import_from_file(file_path, doc_type)
            if result["success"]:
                print(f"  ✅ {file_path}: {result['chunks']} 个文档块")
        else:
            print(f"  ⚠️  文件不存在: {file_path}")
    
    # ===== 2. 导入FAQ知识 =====
    print("\n【步骤2】导入FAQ知识...")
    
    faq_knowledge = [
        {
            "title": "账号注册FAQ",
            "text": """如何注册Hifleet账号？
访问官网 www.hifleet.com，点击"注册"按钮，填写邮箱和密码，验证邮箱后即可完成注册。
注意：密码需至少8位，包含字母和数字。验证邮件有效期为24小时。

注册时邮箱填错了怎么办？
如果还未验证邮箱，可以重新注册。如果已验证，请联系客服处理。

忘记密码怎么办？
点击登录页面的"忘记密码"链接，输入注册邮箱，系统会发送重置链接。"""
        },
        {
            "title": "产品功能FAQ",
            "text": """免费版和专业版有什么区别？
免费版：支持10艘船舶管理、7天历史轨迹、基础气象预报。
专业版：无限船舶管理、30天历史轨迹、高级气象预报、API接口、优先技术支持。

如何升级到专业版？
登录后进入"个人中心" -> "会员升级"，选择套餐并完成支付即可。

可以申请退款吗？
购买后7天内可申请全额退款。超过7天按实际使用天数扣除费用。"""
        },
        {
            "title": "船位查询FAQ",
            "text": """如何查询船位？
方法一：输入9位MMSI号码直接查询。
方法二：输入船名，从搜索结果中选择目标船舶。
显示信息包括：经纬度、航速、航向、航行状态、最后更新时间。

查询不到船位怎么办？
可能原因：船舶未开启AIS设备、MMSI输入错误、船舶不在覆盖范围。
建议：确认MMSI正确性，或联系客服协助查询。

船位数据多久更新一次？
通常每10-30分钟更新一次，具体取决于船舶AIS设备状态。"""
        },
        {
            "title": "API使用FAQ",
            "text": """如何获取API密钥？
登录后进入"个人中心" -> "API管理"，点击"生成API密钥"即可获得。

API调用有限制吗？
专业版：10000次/天
企业版：不限

API文档在哪里？
访问 https://www.hifleet.com/docs/api 查看完整API文档。"""
        },
    ]
    
    for faq in faq_knowledge:
        result = importer.import_text(faq["text"], faq["title"], "faq")
        if result["success"]:
            print(f"  ✅ {faq['title']}")
    
    # ===== 3. 导入操作指南 =====
    print("\n【步骤3】导入操作指南...")
    
    user_guides = [
        {
            "title": "船队管理指南",
            "text": """添加船舶到船队：
1. 登录Hifleet平台
2. 点击"船队管理"菜单
3. 点击"添加船舶"按钮
4. 输入船舶MMSI或船名
5. 确认添加

管理船队：
- 批量查看船位
- 设置船舶标签（在航、锚泊等）
- 订阅船舶动态提醒
- 导出船队报告

免费版限制：最多10艘船舶
专业版：无限船舶"""
        },
        {
            "title": "气象查询指南",
            "text": """查询气象信息：
1. 在地图上选择目标区域
2. 点击"气象"图层
3. 选择气象类型（风浪、洋流、能见度等）
4. 查看实时气象数据

气象预报：
- 免费版：3天预报
- 专业版：7天详细预报
- 企业版：15天定制预报"""
        },
    ]
    
    for guide in user_guides:
        result = importer.import_text(guide["text"], guide["title"], "user_guide")
        if result["success"]:
            print(f"  ✅ {guide['title']}")
    
    # ===== 4. 测试检索效果 =====
    print("\n【步骤4】测试知识库检索...")
    
    test_queries = [
        "如何注册账号",
        "专业版价格",
        "怎么查询船位",
        "API如何使用",
        "船队管理功能"
    ]
    
    for query in test_queries:
        print(f"\n  测试查询: {query}")
        result = importer.test_search(query, top_k=3, min_score=0.5)
        if result["success"]:
            print(f"    找到 {len(result['results'])} 个相关结果")
    
    # ===== 5. 导入总结 =====
    print("\n" + "=" * 60)
    print("导入完成！")
    print("=" * 60)
    
    summary = importer.get_import_summary()
    print(f"✅ 总共导入: {summary['total_docs']} 个文档块")
    print(f"📊 知识库表: {summary['table_name']}")
    print(f"\n💡 提示:")
    print(f"  - 使用 knowledge_search 工具进行检索")
    print(f"  - 建议 min_score 设置为 0.5-0.6")
    print(f"  - 建议 top_k 设置为 3-5")


if __name__ == "__main__":
    main()
