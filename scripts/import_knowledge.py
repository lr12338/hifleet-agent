"""
导入Hifleet知识库数据
"""
from coze_coding_dev_sdk import KnowledgeClient, Config, KnowledgeDocument, DataSourceType, ChunkConfig
from coze_coding_utils.runtime_ctx.context import new_context

# 初始化客户端
ctx = new_context(method="import_knowledge")
config = Config()
client = KnowledgeClient(config=config, ctx=ctx)

# 准备知识库数据
documents = [
    # 注册相关
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""Hifleet账号注册流程

1. 访问Hifleet官网 www.hifleet.com
2. 点击右上角"注册"按钮
3. 填写邮箱地址和密码
4. 验证邮箱（系统会发送验证邮件）
5. 完成注册，登录账号

注意事项：
- 邮箱地址将作为您的登录账号
- 密码需要至少8位，包含字母和数字
- 验证邮件有效期为24小时，请及时验证"""
    ),
    
    # 产品功能
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""Hifleet产品功能介绍

基础功能（免费版）：
- 船位查询：支持MMSI查询船舶实时位置
- 船队管理：最多管理10艘船舶
- 历史轨迹：查看最近7天的航行轨迹
- 气象信息：基础气象预报

专业版功能：
- 无限船队管理
- 历史轨迹延长至30天
- 高级气象预报（风浪、洋流）
- AIS数据分析报告
- API接口调用
- 优先技术支持

企业版功能：
- 专业版所有功能
- 定制化开发
- 私有化部署
- 专属客户经理
- SLA服务保障"""
    ),
    
    # 付费说明
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""Hifleet付费说明

专业版价格：
- 月付：999元/月
- 年付：9999元/年（相当于833元/月，节省16%）

企业版价格：
- 根据需求定制，请联系商务

付费方式：
- 支持支付宝、微信支付、银行转账
- 支持开具增值税发票

退款政策：
- 购买后7天内可申请全额退款
- 使用超过7天按实际使用天数扣除费用"""
    ),
    
    # MMSI说明
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""什么是MMSI？

MMSI（Maritime Mobile Service Identity）是海上移动服务识别码，是船舶的唯一识别标识。

MMSI格式：
- 由9位数字组成
- 例如：413123456

如何查询MMSI：
1. 登录Hifleet平台
2. 在搜索框输入船名或呼号
3. 系统会显示对应的MMSI

常见问题：
- 如果查询不到MMSI，可能是该船舶未开启AIS设备
- MMSI是国际统一编码，不可更改
- 每艘船舶有唯一的MMSI"""
    ),
    
    # 船位查询
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""如何查询船位？

方法一：通过MMSI查询
1. 登录Hifleet平台
2. 在搜索框输入9位MMSI号码
3. 点击搜索，即可查看船舶实时位置

方法二：通过船名查询
1. 登录Hifleet平台
2. 在搜索框输入船名
3. 系统会显示匹配的船舶列表
4. 选择目标船舶查看位置

显示信息包括：
- 经纬度坐标
- 航速（节）
- 航向（度）
- 航行状态
- 最后更新时间"""
    ),
    
    # 船队管理
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""如何管理船队？

添加船舶到船队：
1. 登录Hifleet平台
2. 点击"船队管理"菜单
3. 点击"添加船舶"按钮
4. 输入船舶MMSI或船名
5. 确认添加

船队管理功能：
- 批量查看船位
- 设置船舶标签（如：在航、锚泊）
- 订阅船舶动态提醒
- 导出船队报告

免费版限制：最多管理10艘船舶
专业版：无限船舶管理"""
    ),
    
    # API使用
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""Hifleet API使用说明

API功能：
- 查询船位
- 更新船舶信息
- 获取历史轨迹
- 气象数据查询

如何获取API密钥：
1. 登录Hifleet平台
2. 进入"个人中心" → "API管理"
3. 点击"生成API密钥"
4. 复制密钥并妥善保管

API调用限制：
- 专业版：10000次/天
- 企业版：不限

API文档：https://www.hifleet.com/docs/api"""
    ),
    
    # 技术支持
    KnowledgeDocument(
        source=DataSourceType.TEXT,
        raw_data="""Hifleet技术支持

客服热线：400-123-4567
工作时间：周一至周五 9:00-18:00

在线客服：
- 登录平台后点击右下角客服图标
- 7x24小时在线

技术支持邮箱：support@hifleet.com

常见问题解答：
- 访问 help.hifleet.com
- 查看FAQ和视频教程"""
    ),
]

# 分块配置
chunk_config = ChunkConfig(
    separator="\n\n",
    max_tokens=1000,
    remove_extra_spaces=True
)

# 导入文档
response = client.add_documents(
    documents=documents,
    table_name="coze_doc_knowledge",
    chunk_config=chunk_config
)

if response.code == 0:
    print(f"✅ 成功导入 {len(response.doc_ids)} 条文档到知识库")
    print(f"文档ID: {response.doc_ids}")
else:
    print(f"❌ 导入失败: {response.msg}")
