#!/usr/bin/env python3
"""
Hifleet知识库同步脚本
定期从Hifleet官网抓取内容并导入知识库

使用方式：
    python scripts/sync_hifleet_knowledge.py

建议定时任务：
    每周日凌晨3点执行一次
    crontab: 0 3 * * 0 cd /workspace/projects && python scripts/sync_hifleet_knowledge.py
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import List, Dict, Any

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from coze_coding_dev_sdk.fetch import FetchClient
from coze_coding_dev_sdk.knowledge import KnowledgeClient
from coze_coding_dev_sdk import Config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/app/work/logs/bypass/knowledge_sync.log')
    ]
)
logger = logging.getLogger(__name__)


# Hifleet网站URL配置
HIFLEET_URLS = {
    "helpcenter": [
        {
            "url": "https://www.hifleet.com/helpcenter/?i18n=zh",
            "category": "帮助中心",
            "description": "Hifleet帮助中心首页"
        },
    ],
    "product": [
        {
            "url": "https://www.hifleet.com/",
            "category": "产品介绍",
            "description": "Hifleet官网首页"
        },
    ],
    "community": [
        {
            "url": "https://www.hifleet.com/wp/communityCas",
            "category": "社区模块",
            "description": "Hifleet社区（可能需要登录）"
        },
    ]
}

# 知识库配置
KNOWLEDGE_DATASET = "hifleet_knowledge"


class KnowledgeSynchronizer:
    """知识库同步器"""
    
    def __init__(self):
        """初始化同步器"""
        self.fetch_client = FetchClient()
        self.knowledge_client = KnowledgeClient(Config())
        self.sync_stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
    
    def sync_all(self) -> Dict[str, Any]:
        """
        同步所有配置的URL
        
        Returns:
            同步统计信息
        """
        logger.info("=" * 60)
        logger.info(f"开始同步Hifleet知识库 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        for page_type, url_configs in HIFLEET_URLS.items():
            logger.info(f"\n处理页面类型: {page_type}")
            logger.info("-" * 60)
            
            for url_config in url_configs:
                self.sync_url(url_config)
        
        # 打印统计信息
        self._print_stats()
        
        return self.sync_stats
    
    def sync_url(self, url_config: Dict[str, str]) -> bool:
        """
        同步单个URL
        
        Args:
            url_config: URL配置
            
        Returns:
            是否成功
        """
        url = url_config["url"]
        category = url_config["category"]
        description = url_config["description"]
        
        logger.info(f"正在处理: {description}")
        logger.info(f"URL: {url}")
        
        try:
            # 1. 抓取网页内容
            logger.info("步骤1: 抓取网页内容...")
            response = self.fetch_client.fetch(url=url)
            
            if response.status_code != 0:
                logger.error(f"抓取失败: {response.status_message}")
                self.sync_stats["failed"] += 1
                return False
            
            # 2. 提取文本内容
            logger.info("步骤2: 提取文本内容...")
            text_parts = []
            for item in response.content:
                if item.type == "text":
                    text = item.text.strip()
                    if text:
                        text_parts.append(text)
            
            if not text_parts:
                logger.warning("未提取到文本内容，跳过")
                self.sync_stats["skipped"] += 1
                return False
            
            full_content = "\n\n".join(text_parts)
            
            # 3. 构建知识库条目
            logger.info("步骤3: 构建知识库条目...")
            knowledge_entry = self._build_knowledge_entry(
                title=response.title,
                url=url,
                category=category,
                content=full_content,
                publish_time=response.publish_time
            )
            
            # 4. 导入知识库
            logger.info("步骤4: 导入知识库...")
            success = self._add_to_knowledge(knowledge_entry)
            
            if success:
                logger.info(f"✓ 成功同步: {response.title}")
                self.sync_stats["success"] += 1
            else:
                logger.error(f"✗ 导入知识库失败")
                self.sync_stats["failed"] += 1
            
            self.sync_stats["total"] += 1
            return success
            
        except Exception as e:
            logger.error(f"同步失败: {str(e)}", exc_info=True)
            self.sync_stats["failed"] += 1
            self.sync_stats["total"] += 1
            return False
    
    def _build_knowledge_entry(
        self,
        title: str,
        url: str,
        category: str,
        content: str,
        publish_time: str = None
    ) -> str:
        """
        构建知识库条目内容
        
        Args:
            title: 页面标题
            url: 页面URL
            category: 分类
            content: 正文内容
            publish_time: 发布时间
            
        Returns:
            格式化的知识库条目
        """
        # 添加元数据
        metadata = f"""【分类】{category}
【标题】{title or '未知'}
【来源】{url}
【同步时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        if publish_time:
            metadata += f"【发布时间】{publish_time}\n"
        
        # 组合完整内容
        entry = f"""{metadata}
{'=' * 60}

{content}
"""
        
        return entry
    
    def _add_to_knowledge(self, content: str) -> bool:
        """
        将内容添加到知识库
        
        Args:
            content: 要添加的内容
            
        Returns:
            是否成功
        """
        try:
            # 使用knowledge client添加内容
            response = self.knowledge_client.add(
                dataset=KNOWLEDGE_DATASET,
                content=content
            )
            
            # 检查响应（假设成功响应有特定标志）
            if hasattr(response, 'code') and response.code != 0:
                logger.error(f"知识库添加失败: {response.msg}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"知识库添加异常: {str(e)}", exc_info=True)
            return False
    
    def _print_stats(self):
        """打印同步统计信息"""
        logger.info("\n" + "=" * 60)
        logger.info("同步完成统计")
        logger.info("=" * 60)
        logger.info(f"总计处理: {self.sync_stats['total']}")
        logger.info(f"成功: {self.sync_stats['success']}")
        logger.info(f"失败: {self.sync_stats['failed']}")
        logger.info(f"跳过: {self.sync_stats['skipped']}")
        
        success_rate = 0
        if self.sync_stats['total'] > 0:
            success_rate = (self.sync_stats['success'] / self.sync_stats['total']) * 100
        
        logger.info(f"成功率: {success_rate:.1f}%")
        logger.info("=" * 60)


def main():
    """主函数"""
    try:
        synchronizer = KnowledgeSynchronizer()
        stats = synchronizer.sync_all()
        
        # 返回退出码
        if stats["failed"] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        logger.error(f"同步脚本异常退出: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
