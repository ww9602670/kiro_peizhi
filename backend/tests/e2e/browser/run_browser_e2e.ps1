# 浏览器E2E测试运行脚本

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "浏览器E2E测试 - 环境检查和运行" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查Playwright是否安装
Write-Host "[1/5] 检查Playwright安装..." -ForegroundColor Yellow
python -c "import playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ⚠️ Playwright未安装，正在安装..." -ForegroundColor Yellow
    pip install playwright pytest-playwright
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ Playwright安装成功" -ForegroundColor Green
        Write-Host "  正在安装Chromium浏览器..." -ForegroundColor Yellow
        playwright install chromium
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✓ Chromium安装成功" -ForegroundColor Green
        } else {
            Write-Host "  ✗ Chromium安装失败" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  ✗ Playwright安装失败" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  ✓ Playwright已安装" -ForegroundColor Green
}

# 2. 创建截图目录
Write-Host ""
Write-Host "[2/5] 检查截图目录..." -ForegroundColor Yellow
if (-not (Test-Path "screenshots")) {
    mkdir screenshots | Out-Null
    Write-Host "  ✓ 截图目录已创建: screenshots/" -ForegroundColor Green
} else {
    Write-Host "  ✓ 截图目录已存在: screenshots/" -ForegroundColor Green
}

# 3. 检查后端服务
Write-Host ""
Write-Host "[3/5] 检查后端服务..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8888/api/v1/health" -TimeoutSec 2 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "  ✓ 后端服务运行正常 (http://localhost:8888)" -ForegroundColor Green
    }
} catch {
    Write-Host "  ✗ 后端服务未运行" -ForegroundColor Red
    Write-Host ""
    Write-Host "请在另一个终端启动后端服务：" -ForegroundColor Yellow
    Write-Host "  cd backend" -ForegroundColor Cyan
    Write-Host "  uvicorn app.main:app --host 0.0.0.0 --port 8888" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# 4. 检查前端服务
Write-Host ""
Write-Host "[4/5] 检查前端服务..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 2 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "  ✓ 前端服务运行正常 (http://localhost:5173)" -ForegroundColor Green
    }
} catch {
    Write-Host "  ✗ 前端服务未运行" -ForegroundColor Red
    Write-Host ""
    Write-Host "请在另一个终端启动前端服务：" -ForegroundColor Yellow
    Write-Host "  cd frontend" -ForegroundColor Cyan
    Write-Host "  pnpm dev" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# 5. 运行测试
Write-Host ""
Write-Host "[5/5] 运行浏览器E2E测试..." -ForegroundColor Yellow
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 运行pytest
pytest tests/e2e/browser/ -v -s

# 检查测试结果
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "✓ 所有测试通过！" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "截图保存在: screenshots/" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "✗ 部分测试失败" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "请查看上方错误信息和截图: screenshots/" -ForegroundColor Yellow
}
