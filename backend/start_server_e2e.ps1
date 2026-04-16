# E2E测试专用后端服务启动脚本
# 确保使用文件数据库而非in-memory数据库

Write-Host "正在启动后端服务 (E2E测试模式)..." -ForegroundColor Green

# 清除可能存在的环境变量
$env:BOCAI_DB_PATH = $null

# 显式设置数据库路径
$env:BOCAI_DB_PATH = "data/bocai.db"

Write-Host "数据库路径: $env:BOCAI_DB_PATH" -ForegroundColor Cyan

# 启动服务
Write-Host "启动uvicorn服务器..." -ForegroundColor Yellow
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
