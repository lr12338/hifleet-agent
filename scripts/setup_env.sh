#!/bin/bash

# Hifleet智能客服环境变量快速配置脚本
# 使用方法: source scripts/setup_env.sh

set -e

echo "======================================"
echo "  Hifleet智能客服环境变量配置向导"
echo "======================================"
echo ""

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 函数：打印成功信息
print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

# 函数：打印错误信息
print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 函数：打印警告信息
print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# 函数：打印信息
print_info() {
    echo -e "ℹ️  $1"
}

# 1. 配置大模型API
print_info "步骤1: 配置大模型API"
read -p "请输入COZE_WORKLOAD_IDENTITY_API_KEY: " COZE_API_KEY

if [ -z "$COZE_API_KEY" ]; then
    print_warning "未输入API KEY，将使用平台自动注入"
else
    export COZE_WORKLOAD_IDENTITY_API_KEY="$COZE_API_KEY"
    print_success "COZE_WORKLOAD_IDENTITY_API_KEY已设置"
fi

# 大模型API地址（默认值）
export COZE_INTEGRATION_MODEL_BASE_URL="https://api.coze.cn"
print_success "COZE_INTEGRATION_MODEL_BASE_URL已设置: $COZE_INTEGRATION_MODEL_BASE_URL"

echo ""

# 2. 配置船舶服务API
print_info "步骤2: 配置船舶服务API"

# API地址（默认值）
export SHIP_SERVICE_API_URL="https://y3rz9srmmb.coze.site/run"
print_success "SHIP_SERVICE_API_URL已设置: $SHIP_SERVICE_API_URL"

# Token
print_info "船舶服务API Token配置方式："
echo "1. 从Coze平台获取完整Token"
echo "2. 或直接输入（以下为示例Token）"
echo ""

# 提供示例Token（用户可以按回车使用）
DEFAULT_TOKEN="eyJhbGciOiJSUzI1NiIsImtpZCI6ImNiYjIxYjQxLWZkMTktNDc0ZS1hNjU5LTc2NGQxZGI4YjA0OSJ9.eyJpc3MiOiJodHRwczovL2FwaS5jb3plLmNuIiwiYXVkIjpbIlAyeWtCdFB2MmJ4c1pmT3VkQms2VjdzZG1XdmlhMXk5Il0sImV4cCI6ODIxMDI2Njg3Njc5OSwiaWF0IjoxNzcwMDg4OTY3LCJzdWIiOiJzcGlmZmU6Ly9hcGkuY296ZS5jbi93b3JrbG9hZF9pZGVudGl0eS9pZDo3NjAyNDcwMjQ1MTU1OTk1NjkxIiwic3JjIjoiaW5ib3VuZF9hdXRoX2FjY2Vzc190b2tlbl9pZDo3NjAyNDc0MjI3OTAzNDk2MjQ0In0.eEic1jIwn8Fia3RBVrrotCDTR0xuG3n66gLVU4M7eepDDzBx5mjyWAlGkSRdzQeWKt5FS91-k7HznNuxSfr_S8-srwUV5HEcgqgSBitT9jc3gKDPogqd0FrR-Gf09tqOOMlVJVj1x6jEvcN3541iOMPFPNHrdaDxPCvwsIwfvJY0NVgbasmuGphY8AVOgyW8l6fRN83MAE6RB3w-PnoTOUj-fXYs95toplne80AyEtUwSqSnqXlA1i3yZd-qu8acFDqRSisCcuthWw3XQuupyUhQQ8NHjsQBzFt-OUbycvraOAsN1wMa_1sDu6LuxpCUOhxMmaVO2PezrckJZ6P1Ww"

read -p "请输入SHIP_SERVICE_API_TOKEN (按回车使用示例Token): " SHIP_TOKEN

if [ -z "$SHIP_TOKEN" ]; then
    export SHIP_SERVICE_API_TOKEN="$DEFAULT_TOKEN"
    print_warning "使用示例Token（仅用于测试）"
else
    export SHIP_SERVICE_API_TOKEN="$SHIP_TOKEN"
    print_success "SHIP_SERVICE_API_TOKEN已设置"
fi

echo ""

# 3. 可选配置：数据库
print_info "步骤3: 数据库配置（可选，按回车跳过）"
read -p "请输入SUPABASE_URL (可选): " SUPABASE_URL_INPUT
read -p "请输入SUPABASE_KEY (可选): " SUPABASE_KEY_INPUT

if [ -n "$SUPABASE_URL_INPUT" ]; then
    export SUPABASE_URL="$SUPABASE_URL_INPUT"
    print_success "SUPABASE_URL已设置"
fi

if [ -n "$SUPABASE_KEY_INPUT" ]; then
    export SUPABASE_KEY="$SUPABASE_KEY_INPUT"
    print_success "SUPABASE_KEY已设置"
fi

echo ""

# 4. 保存到.env文件
print_info "步骤4: 保存配置"
read -p "是否保存到.env文件？(y/n): " SAVE_TO_ENV

if [ "$SAVE_TO_ENV" = "y" ] || [ "$SAVE_TO_ENV" = "Y" ]; then
    cat > .env << EOF
# Hifleet智能客服环境变量配置
# 自动生成于 $(date)

# 大模型API配置
COZE_WORKLOAD_IDENTITY_API_KEY=$COZE_WORKLOAD_IDENTITY_API_KEY
COZE_INTEGRATION_MODEL_BASE_URL=$COZE_INTEGRATION_MODEL_BASE_URL

# 船舶服务API配置
SHIP_SERVICE_API_URL=$SHIP_SERVICE_API_URL
SHIP_SERVICE_API_TOKEN=$SHIP_SERVICE_API_TOKEN
EOF
    
    if [ -n "$SUPABASE_URL" ]; then
        echo "SUPABASE_URL=$SUPABASE_URL" >> .env
    fi
    
    if [ -n "$SUPABASE_KEY" ]; then
        echo "SUPABASE_KEY=$SUPABASE_KEY" >> .env
    fi
    
    print_success ".env文件已创建"
    print_warning "请将.env添加到.gitignore，避免泄露敏感信息"
fi

echo ""

# 5. 验证配置
print_info "步骤5: 验证配置"
echo ""

echo "当前环境变量："
echo "  COZE_WORKLOAD_IDENTITY_API_KEY: ${COZE_WORKLOAD_IDENTITY_API_KEY:0:20}..."
echo "  COZE_INTEGRATION_MODEL_BASE_URL: $COZE_INTEGRATION_MODEL_BASE_URL"
echo "  SHIP_SERVICE_API_URL: $SHIP_SERVICE_API_URL"
echo "  SHIP_SERVICE_API_TOKEN: ${SHIP_SERVICE_API_TOKEN:0:20}..."
echo ""

print_success "环境变量配置完成！"
echo ""
print_info "下一步操作："
echo "1. 运行测试: python scripts/test_api_config.py"
echo "2. 启动服务: python src/main.py"
echo "3. 测试Agent: test_run '查询MMSI 123456789的船位'"
