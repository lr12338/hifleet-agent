"""
Hifleet知识库管理CLI工具
提供知识库的导入、检索、管理等功能
"""
import click
import json
import os
from pathlib import Path
from coze_coding_dev_sdk import KnowledgeClient, Config
from coze_coding_utils.runtime_ctx.context import new_context

# 初始化客户端
ctx = new_context(method="kb_cli")
config = Config()
client = KnowledgeClient(config=config, ctx=ctx)


@click.group()
def cli():
    """Hifleet知识库管理工具"""
    pass


@cli.command()
@click.option('--file', '-f', type=click.Path(exists=True), help='要导入的文件路径')
@click.option('--text', '-t', help='直接导入的文本内容')
@click.option('--url', '-u', help='要导入的URL')
@click.option('--title', help='文档标题')
@click.option('--type', 'doc_type', default='product_doc', 
              type=click.Choice(['product_doc', 'faq', 'api_doc', 'user_guide']),
              help='文档类型')
@click.option('--table', default='coze_doc_knowledge', help='知识库表名')
def import_doc(file, text, url, title, doc_type, table):
    """导入文档到知识库"""
    from scripts.enhanced_import_knowledge import HifleetKnowledgeImporter
    
    importer = HifleetKnowledgeImporter(table_name=table)
    
    if file:
        click.echo(f"📥 导入文件: {file}")
        result = importer.import_from_file(file, doc_type)
    elif text:
        click.echo(f"📥 导入文本: {title or '未命名'}")
        result = importer.import_text(text, title or "未命名文档", doc_type)
    elif url:
        click.echo(f"📥 导入URL: {url}")
        result = importer.import_from_url(url, doc_type)
    else:
        click.echo("❌ 请指定要导入的内容（--file, --text 或 --url）")
        return
    
    if result.get("success"):
        click.echo(f"✅ 导入成功！文档ID: {result.get('doc_ids', [])}")
    else:
        click.echo(f"❌ 导入失败: {result.get('error')}")


@cli.command()
@click.argument('query')
@click.option('--top-k', default=5, help='返回结果数量')
@click.option('--min-score', default=0.6, help='最小相似度阈值')
@click.option('--table', default='coze_doc_knowledge', help='知识库表名')
def search(query, top_k, min_score, table):
    """检索知识库"""
    click.echo(f"🔍 检索: {query}")
    click.echo(f"   参数: top_k={top_k}, min_score={min_score}")
    click.echo()
    
    response = client.search(
        query=query,
        top_k=top_k,
        min_score=min_score
    )
    
    if response.code != 0:
        click.echo(f"❌ 检索失败: {response.msg}")
        return
    
    if not response.chunks:
        click.echo("未找到相关结果")
        return
    
    click.echo(f"找到 {len(response.chunks)} 个结果：\n")
    
    for i, chunk in enumerate(response.chunks, 1):
        click.echo(f"━━━ 结果 {i} ━━━")
        click.echo(f"相关度: {chunk.score:.2%}")
        click.echo(f"文档ID: {chunk.doc_id}")
        click.echo(f"内容:\n{chunk.content[:300]}...")
        click.echo()


@cli.command()
@click.option('--table', default='coze_doc_knowledge', help='知识库表名')
def stats(table):
    """查看知识库统计信息"""
    # 注意：SDK可能不提供直接的统计接口，这里只是示例
    click.echo(f"📊 知识库统计信息")
    click.echo(f"   表名: {table}")
    click.echo()
    click.echo("💡 提示:")
    click.echo("   - 使用 'kb search <query>' 测试检索效果")
    click.echo("   - 使用 'kb import --file <path>' 导入新文档")


@cli.command()
def batch_import():
    """批量导入Hifleet知识库"""
    from scripts.enhanced_import_knowledge import main
    
    click.echo("🚀 开始批量导入知识库...")
    click.echo()
    
    main()


# 自定义命令：测试检索效果
@cli.command()
@click.option('--queries', help='测试查询列表（JSON格式）')
def test_search(queries):
    """测试知识库检索效果"""
    test_queries = [
        "如何注册账号",
        "专业版价格",
        "怎么查询船位",
        "API如何使用",
        "船队管理功能"
    ]
    
    if queries:
        try:
            test_queries = json.loads(queries)
        except:
            click.echo("⚠️ JSON格式错误，使用默认测试查询")
    
    click.echo("🧪 知识库检索测试\n")
    
    for query in test_queries:
        click.echo(f"━━━ 测试: {query} ━━━")
        
        response = client.search(query=query, top_k=3, min_score=0.5)
        
        if response.code == 0 and response.chunks:
            click.echo(f"✅ 找到 {len(response.chunks)} 个结果")
            click.echo(f"   最高相似度: {response.chunks[0].score:.2%}")
        else:
            click.echo("❌ 未找到相关结果")
        click.echo()


if __name__ == '__main__':
    cli()
