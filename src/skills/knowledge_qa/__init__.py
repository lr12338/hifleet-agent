"""知识问答技能"""
from skills.knowledge_qa.tools import local_kb_search, smart_search, web_search, web_search_agent_browser

SKILL_TOOLS = [local_kb_search, web_search, web_search_agent_browser, smart_search]
