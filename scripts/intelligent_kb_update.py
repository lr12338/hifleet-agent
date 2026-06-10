#!/usr/bin/env python3
"""
智能知识库更新脚本
将更新文档智能导入到Agent知识库中，自动去重、分类和更新。

功能：
1. 解析更新文档，提取独立知识条目
2. 与现有知识库比对，识别重复/冲突内容
3. 自动分类：FAQ(outputs) vs 主题页(wiki)
4. 生成更新报告，人工确认后执行

使用方式：
    python scripts/intelligent_kb_update.py docs/RAG/update/update.txt
    python scripts/intelligent_kb_update.py docs/RAG/update/update.txt --dry-run  # 仅分析，不修改
    python scripts/intelligent_kb_update.py docs/RAG/update/update.txt --auto      # 自动执行，无需确认
"""
import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from coze_coding_dev_sdk import LLMClient, Config
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import SystemMessage, HumanMessage


# ======================== 配置 ========================

RAG_DIR = Path(project_root) / "docs" / "RAG"
OUTPUTS_DIR = RAG_DIR / "hifleet_cs_outputs"
WIKI_DIR = RAG_DIR / "hifleet_cs_wiki"
JSONL_FILE = OUTPUTS_DIR / "客服知识库结构化.jsonl"

LLM_MODEL = "doubao-seed-2-0-lite-260215"  # 使用均衡型模型


# ======================== 工具函数 ========================

def get_llm_response(system_prompt: str, user_message: str) -> str:
    """调用LLM获取响应"""
    client = LLMClient(ctx=new_context(method="kb_update"))
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    response = client.invoke(
        messages=messages,
        model=LLM_MODEL,
        temperature=0.1,  # 低温度，确保结构化输出
        max_completion_tokens=8192,
    )
    content = response.content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def load_existing_jsonl() -> List[Dict]:
    """加载现有JSONL知识库"""
    items = []
    if not JSONL_FILE.exists():
        return items
    with open(JSONL_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items


def load_existing_wiki_files() -> Dict[str, str]:
    """加载现有wiki主题页"""
    wiki_files = {}
    if not WIKI_DIR.exists():
        return wiki_files
    for f in WIKI_DIR.glob("*.md"):
        if f.name == "INDEX.md":
            continue
        with open(f, 'r', encoding='utf-8') as fh:
            wiki_files[f.stem] = fh.read()
    return wiki_files


def get_next_kb_id(existing_items: List[Dict]) -> str:
    """获取下一个可用的KB ID"""
    max_id = 0
    for item in existing_items:
        kid = item.get("id", "kb_000")
        try:
            num = int(kid.replace("kb_", ""))
            max_id = max(max_id, num)
        except ValueError:
            pass
    return f"kb_{max_id + 1:03d}"


def parse_update_doc(file_path: str) -> List[Dict]:
    """
    使用LLM解析更新文档，提取独立知识条目
    返回格式: [{"title": "...", "content": "...", "type": "faq|wiki"}]
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        doc_content = f.read()

    system_prompt = """你是HiFleet航运平台的客服知识库管理专家。
你的任务是解析一份更新文档，将其拆分为独立的知识条目。

拆分规则：
1. 每个独立的问题-答案对为一个FAQ条目
2. 每个产品/功能介绍为一个Wiki条目
3. 如果同一段落既包含问题又包含产品介绍，分别拆分为FAQ和Wiki
4. 保留原文的核心信息，不遗漏关键细节

输出严格的JSON格式：
```json
[
  {
    "title": "简短标题（不超过20字）",
    "content": "完整内容原文",
    "type": "faq",
    "possible_question": "用户可能会怎么问这个问题？"
  },
  {
    "title": "简短标题",
    "content": "完整内容原文",
    "type": "wiki",
    "possible_question": null
  }
]
```

注意：
- type只能是"faq"或"wiki"
- faq类型必须有possible_question
- 如果一段内容同时包含Q&A和产品介绍，拆分为2个条目
- 不要合并不同主题的内容"""

    response = get_llm_response(system_prompt, doc_content)

    # 提取JSON
    try:
        # 尝试从markdown代码块中提取
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()
        else:
            json_str = response.strip()
        entries = json.loads(json_str)
    except (json.JSONDecodeError, IndexError) as e:
        print(f"⚠️ LLM返回JSON解析失败: {e}")
        print(f"原始响应: {response[:500]}...")
        # 降级：手动提取
        entries = _manual_parse(doc_content)

    return entries


def _manual_parse(doc_content: str) -> List[Dict]:
    """降级：手动解析更新文档"""
    entries = []
    sections = doc_content.split("# ")

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # 提取标题
        lines = section.split("\n")
        title = lines[0].strip()

        # 判断类型
        if title.startswith("问题") or "是什么" in title or "怎么" in title:
            entry_type = "faq"
            # 提取问题
            possible_question = title
            # 如果有多行，第二行可能是答案
            content = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        else:
            entry_type = "wiki"
            possible_question = None
            content = section

        entries.append({
            "title": title[:20],
            "content": content,
            "type": entry_type,
            "possible_question": possible_question,
        })

    return entries


def check_duplicates(
    new_entries: List[Dict],
    existing_jsonl: List[Dict],
    existing_wiki: Dict[str, str],
) -> List[Dict]:
    """
    使用LLM检测新条目与现有知识库的重复/冲突
    为每个新条目添加duplication_info字段
    """
    # 构建现有知识库摘要
    jsonl_summary = []
    for item in existing_jsonl:
        jsonl_summary.append({
            "id": item.get("id"),
            "question": item.get("question", ""),
            "answer": item.get("answer", "")[:100],
            "keywords": item.get("keywords", []),
        })

    wiki_summary = []
    for name, content in existing_wiki.items():
        wiki_summary.append({
            "name": name,
            "preview": content[:150].replace("\n", " "),
        })

    existing_summary = json.dumps({
        "faq_entries": jsonl_summary,
        "wiki_pages": wiki_summary,
    }, ensure_ascii=False, indent=2)

    new_entries_json = json.dumps(new_entries, ensure_ascii=False, indent=2)

    system_prompt = """你是知识库去重专家。对比新条目和现有知识库，判断每个新条目的状态。

对每个新条目，判断：
1. "new" - 全新内容，无重复
2. "update" - 与现有条目部分重复，需要更新（补充新信息或修正错误）
3. "duplicate" - 完全重复，无需添加
4. "conflict" - 与现有条目冲突（信息不一致），需要用新内容覆盖旧内容

输出严格的JSON格式：
```json
[
  {
    "index": 0,
    "status": "new|update|duplicate|conflict",
    "matched_existing_id": "kb_xxx 或 wiki文件名（如果匹配到）",
    "reason": "判断理由",
    "action": "add|update|skip|override"
  }
]
```

注意：
- 如果新条目与旧条目主题相同但信息不同（如更正错误），标记为"conflict"
- 如果新条目是对旧条目的补充扩展，标记为"update"
- 完全相同的内容标记为"duplicate"并建议skip"""

    response = get_llm_response(system_prompt, 
        f"## 现有知识库\n{existing_summary}\n\n## 新条目\n{new_entries_json}")

    try:
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()
        else:
            json_str = response.strip()
        dup_results = json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        print("⚠️ 去重分析JSON解析失败，默认全部标记为new")
        dup_results = [
            {"index": i, "status": "new", "matched_existing_id": None,
             "reason": "LLM解析失败，默认新增", "action": "add"}
            for i in range(len(new_entries))
        ]

    # 合并去重结果到新条目
    for result in dup_results:
        idx = result.get("index", -1)
        if 0 <= idx < len(new_entries):
            new_entries[idx]["duplication_info"] = result

    return new_entries


def generate_faq_entries(
    new_entries: List[Dict],
    existing_jsonl: List[Dict],
) -> List[Dict]:
    """
    使用LLM将FAQ类型的新条目格式化为标准JSONL格式
    """
    faq_entries = [e for e in new_entries if e.get("type") == "faq"
                   and e.get("duplication_info", {}).get("action") != "skip"]

    if not faq_entries:
        return []

    # 构建现有分类参考
    existing_categories = list(set(item.get("category", "") for item in existing_jsonl))
    next_id = get_next_kb_id(existing_jsonl)

    faq_json = json.dumps(faq_entries, ensure_ascii=False, indent=2)
    categories_str = ", ".join(existing_categories)

    system_prompt = f"""你是HiFleet客服知识库结构化专家。将原始知识条目转换为标准的JSONL格式。

现有分类参考: {categories_str}
起始ID: {next_id}

输出严格的JSON数组格式：
```json
[
  {{
    "id": "{next_id}",
    "category": "从现有分类中选择或新建",
    "intent": "英文意图标识（如green_dot_meaning）",
    "question": "用户可能问的问题（简洁自然）",
    "answer": "标准答案（基于原文，简洁准确）",
    "keywords": ["关键词1", "关键词2"],
    "related_topics": ["相关主题1"],
    "sources": ["来源文档"],
    "escalate_when": ["转人工场景1"]
  }}
]
```

规则：
- question: 应该是用户自然提问的方式，不超过30字
- answer: 简洁准确，保留核心信息，不超过200字
- keywords: 3-8个检索关键词
- related_topics: 对应wiki主题页名称
- escalate_when: 何时建议转人工"""

    response = get_llm_response(system_prompt, faq_json)

    try:
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()
        else:
            json_str = response.strip()
        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        print("⚠️ FAQ格式化JSON解析失败")
        return []


def generate_wiki_content(
    new_entries: List[Dict],
) -> List[Dict]:
    """
    使用LLM将Wiki类型的新条目格式化为Markdown主题页
    """
    wiki_entries = [e for e in new_entries if e.get("type") == "wiki"
                    and e.get("duplication_info", {}).get("action") != "skip"]

    if not wiki_entries:
        return []

    wiki_json = json.dumps(wiki_entries, ensure_ascii=False, indent=2)

    system_prompt = """你是HiFleet客服知识库wiki页面编写专家。将原始产品内容转换为结构化的Markdown主题页。

输出JSON数组：
```json
[
  {
    "filename": "岸基值班与船舶点验",
    "content": "# 标题\\n\\n## 概述\\n...\\n\\n## 核心功能\\n...\\n\\n## 适用场景\\n..."
  }
]
```

规则：
- filename: 简洁中文，与现有wiki文件命名风格一致
- content: 结构化Markdown，使用##和###分节
- 保留原文所有关键信息，不遗漏
- 语言专业简洁，适合客服参考"""

    response = get_llm_response(system_prompt, wiki_json)

    try:
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()
        else:
            json_str = response.strip()
        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        print("⚠️ Wiki格式化JSON解析失败")
        return []


def update_jsonl(
    new_faq_items: List[Dict],
    existing_items: List[Dict],
    conflict_resolutions: Dict[str, str],
) -> List[Dict]:
    """
    更新JSONL文件
    - 新条目追加
    - 冲突条目覆盖
    """
    updated = list(existing_items)

    # 处理冲突（覆盖现有条目）
    for new_item in new_faq_items:
        conflict_id = conflict_resolutions.get(new_item.get("id"))
        if conflict_id:
            for i, existing in enumerate(updated):
                if existing.get("id") == conflict_id:
                    # 保留原ID，更新内容
                    new_item["id"] = conflict_id
                    updated[i] = new_item
                    print(f"  🔄 覆盖更新: {conflict_id} - {new_item.get('question', '')[:30]}")
                    break
        else:
            # 新增
            updated.append(new_item)
            print(f"  ➕ 新增: {new_item.get('id')} - {new_item.get('question', '')[:30]}")

    return updated


def save_jsonl(items: List[Dict]):
    """保存JSONL文件"""
    with open(JSONL_FILE, 'w', encoding='utf-8') as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def save_wiki_file(filename: str, content: str):
    """保存wiki Markdown文件"""
    filepath = WIKI_DIR / f"{filename}.md"
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def print_update_report(entries: List[Dict]):
    """打印更新分析报告"""
    print("\n" + "=" * 70)
    print("📋 知识库更新分析报告")
    print("=" * 70)

    status_counts = {"new": 0, "update": 0, "duplicate": 0, "conflict": 0}
    for entry in entries:
        info = entry.get("duplication_info", {})
        status = info.get("status", "unknown")
        if status in status_counts:
            status_counts[status] += 1

    print(f"\n📊 统计:")
    print(f"  全新条目: {status_counts['new']}")
    print(f"  需要更新: {status_counts['update']}")
    print(f"  完全重复: {status_counts['duplicate']}")
    print(f"  信息冲突: {status_counts['conflict']}")

    print(f"\n📝 详细分析:")
    for i, entry in enumerate(entries):
        info = entry.get("duplication_info", {})
        status = info.get("status", "unknown")
        action = info.get("action", "unknown")
        reason = info.get("reason", "无说明")
        matched = info.get("matched_existing_id", "")

        status_icon = {
            "new": "🆕", "update": "🔄", "duplicate": "♻️", "conflict": "⚠️"
        }.get(status, "❓")

        print(f"\n  {status_icon} [{status.upper()}] {entry.get('title', '未命名')}")
        print(f"    类型: {entry.get('type')}")
        print(f"    操作: {action}")
        if matched:
            print(f"    匹配: {matched}")
        print(f"    理由: {reason}")


def run_update(
    update_file: str,
    dry_run: bool = False,
    auto: bool = False,
):
    """执行智能更新"""
    print("=" * 70)
    print("🚀 HiFleet 知识库智能更新工具")
    print("=" * 70)
    print(f"\n📄 更新文档: {update_file}")
    print(f"🔍 模式: {'预览分析' if dry_run else '自动执行' if auto else '交互确认'}")

    # Step 1: 加载现有知识库
    print("\n【Step 1】加载现有知识库...")
    existing_jsonl = load_existing_jsonl()
    existing_wiki = load_existing_wiki_files()
    print(f"  现有FAQ条目: {len(existing_jsonl)}")
    print(f"  现有Wiki页面: {len(existing_wiki)}")

    # Step 2: 解析更新文档
    print("\n【Step 2】解析更新文档...")
    new_entries = parse_update_doc(update_file)
    print(f"  提取到 {len(new_entries)} 个知识条目:")
    for i, entry in enumerate(new_entries):
        print(f"    [{entry.get('type')}] {entry.get('title', '未命名')}")

    # Step 3: 去重检测
    print("\n【Step 3】检测重复与冲突...")
    new_entries = check_duplicates(new_entries, existing_jsonl, existing_wiki)

    # 打印报告
    print_update_report(new_entries)

    if dry_run:
        print("\n✅ 预览模式，未做任何修改。")
        return

    # Step 4: 确认执行
    if not auto:
        print("\n" + "-" * 70)
        confirm = input("是否执行更新？(y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 已取消更新。")
            return

    # Step 5: 生成标准格式
    print("\n【Step 4】生成标准知识库格式...")

    # 生成FAQ条目
    print("  生成FAQ条目...")
    faq_items = generate_faq_entries(new_entries, existing_jsonl)
    print(f"  生成 {len(faq_items)} 个FAQ条目")

    # 生成Wiki页面
    print("  生成Wiki页面...")
    wiki_items = generate_wiki_content(new_entries)
    print(f"  生成 {len(wiki_items)} 个Wiki页面")

    # Step 6: 构建冲突覆盖映射
    conflict_resolutions = {}
    for entry in new_entries:
        info = entry.get("duplication_info", {})
        if info.get("status") == "conflict" and info.get("action") == "override":
            matched_id = info.get("matched_existing_id", "")
            if matched_id and matched_id.startswith("kb_"):
                # 新FAQ将覆盖此ID的旧条目
                for fi in faq_items:
                    conflict_resolutions[fi.get("id", "")] = matched_id

    # Step 7: 执行更新
    print("\n【Step 5】执行知识库更新...")

    # 更新JSONL
    if faq_items:
        updated_jsonl = update_jsonl(faq_items, existing_jsonl, conflict_resolutions)
        save_jsonl(updated_jsonl)
        print(f"  ✅ JSONL已更新: {len(existing_jsonl)} → {len(updated_jsonl)} 条")

    # 保存Wiki
    for wiki_item in wiki_items:
        filename = wiki_item.get("filename", "未命名")
        content = wiki_item.get("content", "")
        save_wiki_file(filename, content)
        print(f"  ✅ Wiki已保存: {filename}.md")

    # 更新术语速查表（如果有冲突修正）
    _update_glossary_from_conflicts(new_entries)

    print("\n" + "=" * 70)
    print("✅ 知识库更新完成！")
    print("=" * 70)
    print("\n💡 下一步操作:")
    print("  1. 运行 python scripts/import_rag_knowledge.py 重新导入向量库")
    print("  2. 运行 test_run 测试Agent回答效果")


def _update_glossary_from_conflicts(new_entries: List[Dict]):
    """根据冲突修正结果，提示更新术语速查表"""
    conflicts = [
        e for e in new_entries
        if e.get("duplication_info", {}).get("status") == "conflict"
    ]

    if not conflicts:
        return

    print("\n⚠️  检测到信息冲突，以下内容可能需要同步更新术语速查表：")
    for entry in conflicts:
        info = entry["duplication_info"]
        print(f"  - {entry.get('title')}: {info.get('reason')}")
    print("  请手动检查 src/tools/knowledge_search_tool.py 中的 PLATFORM_GLOSSARY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HiFleet知识库智能更新工具")
    parser.add_argument("update_file", help="更新文档路径")
    parser.add_argument("--dry-run", action="store_true", help="仅分析，不修改文件")
    parser.add_argument("--auto", action="store_true", help="自动执行，无需确认")
    args = parser.parse_args()

    if not os.path.exists(args.update_file):
        print(f"❌ 文件不存在: {args.update_file}")
        sys.exit(1)

    run_update(args.update_file, dry_run=args.dry_run, auto=args.auto)
