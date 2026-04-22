# 完整的浏览器E2E测试准备和运行脚本
# 此脚本会：
# 1. 停止所有运行中的策略
# 2. 清理测试数据
# 3. 创建新的测试账号
# 4. 检查前后端服务状态
# 5. 安装Playwright（如果需要）
# 6. 运行浏览器E2E测试

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "浏览器E2E测试 - 完整准备和运行脚本" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 步骤1: 准备测试环境
Write-Host "[步骤1] 准备测试环境..." -ForegroundColor Yellow
Write-Host "  - 停止所有运行中的策略" -ForegroundColor Gray
Write-Host "  - 清理旧测试数据" -ForegroundColor Gray
Write-Host "  - 创建新的测试账号" -ForegroundColor Gray
Write-Host ""

python prepare_browser_tests.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 环境准备失败" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "✓ 环境准备完成" -ForegroundColor Green
Write-Host ""

# 步骤2: 检查后端服务
Write-Host "[步骤2] 检查后端服务..." -ForegroundColor Yellow
$backendRunning = $false
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8888/api/v1/health" -Method GET -TimeoutSec 2 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        $backendRunning = $true
        Write-Host "  ✓ 后端服务正在运行 (http://localhost:8888)" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️ 后端服务未运行" -ForegroundColor Yellow
}

if (-not $backendRunning) {
    Write-Host ""
    Write-Host "请在另一个终端启动后端服务:" -ForegroundColor Cyan
    Write-Host "  cd backend" -ForegroundColor White
    Write-Host "  uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload" -ForegroundColor White
    Write-Host ""
    Write-Host "按任意键继续..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# 步骤3: 检查前端服务
Write-Host ""
Write-Host "[步骤3] 检查前端服务..." -ForegroundColor Yellow
$frontendRunning = $false
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5173" -Method GET -TimeoutSec 2 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        $frontendRunning = $true
        Write-Host "  ✓ 前端服务正在运行 (http://localhost:5173)" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️ 前端服务未运行" -ForegroundColor Yellow
}

if (-not $frontendRunning) {
    Write-Host ""
    Write-Host "请在另一个终端启动前端服务:" -ForegroundColor Cyan
    Write-Host "  cd frontend" -ForegroundColor White
    Write-Host "  pnpm dev" -ForegroundColor White
    Write-Host ""
    Write-Host "按任意键继续..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# 步骤4: 检查Playwright安装
Write-Host ""
Write-Host "[步骤4] 检查Playwright安装..." -ForegroundColor Yellow
python -c "import playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ⚠️ Playwright未安装，正在安装..." -ForegroundColor Yellow
    pip install playwright pytest-playwright
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ❌ Playwright安装失败" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "  正在安装Chromium浏览器..." -ForegroundColor Yellow
    playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ❌ Chromium安装失败" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "  ✓ Playwright安装完成" -ForegroundColor Green
} else {
    Write-Host "  ✓ Playwright已安装" -ForegroundColor Green
}

# 步骤5: 创建截图目录
Write-Host ""
Write-Host "[步骤5] 创建截图目录..." -ForegroundColor Yellow
if (-not (Test-Path "screenshots")) {
    New-Item -ItemType Directory -Path "screenshots" | Out-Null
    Write-Host "  ✓ 截图目录已创建" -ForegroundColor Green
} else {
    Write-Host "  ✓ 截图目录已存在" -ForegroundColor Green
}

# 步骤6: 运行浏览器E2E测试
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "[步骤6] 运行浏览器E2E测试" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "测试账号信息:" -ForegroundColor Yellow
Write-Host "  用户名: e2e_browser_test" -ForegroundColor White
Write-Host "  密码: test123456" -ForegroundColor White
Write-Host ""
Write-Host "开始运行测试..." -ForegroundColor Yellow
Write-Host ""

pytest tests/e2e/browser/test_full_workflow.py -v -s

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "✓ 所有测试通过！" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "查看测试截图:" -ForegroundColor Yellow
    Write-Host "  screenshots/full-workflow-*.png" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "❌ 测试失败" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "请检查:" -ForegroundColor Yellow
    Write-Host "  1. 后端服务是否正常运行" -ForegroundColor White
    Write-Host "  2. 前端服务是否正常运行" -ForegroundColor White
    Write-Host "  3. 测试账号是否正确创建" -ForegroundColor White
    Write-Host "  4. 查看测试输出和截图" -ForegroundColor White
    Write-Host ""
}
