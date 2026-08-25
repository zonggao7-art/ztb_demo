# ============================================================
# ztb_clean 数据库部署脚本 (Windows PowerShell)
# 功能：
#   1. 通过 Docker Compose 启动 MySQL 容器
#   2. 等待 MySQL 就绪后执行 DDL 建表
#   3. 从 raw_tables/ CSV 文件导入数据
#   4. 构建 FULLTEXT 索引
#   5. 验证数据完整性
# ============================================================
# 用法：
#   .\scripts\deploy_ztb_clean.ps1                  # 完整部署
#   .\scripts\deploy_ztb_clean.ps1 -InitOnly        # 仅启动容器+建表
#   .\scripts\deploy_ztb_clean.ps1 -ImportOnly      # 仅导入数据（容器已运行）
#   .\scripts\deploy_ztb_clean.ps1 -VerifyOnly      # 仅验证数据
#   .\scripts\deploy_ztb_clean.ps1 -CleanDeploy     # 清空重建
# ============================================================

param(
    [switch]$InitOnly,
    [switch]$ImportOnly,
    [switch]$VerifyOnly,
    [switch]$CleanDeploy
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$ComposeFile = Join-Path $ProjectDir "docker\mysql\docker-compose.yml"

# ── 配置 ──
$MYSQL_HOST = if ($env:MYSQL_HOST) { $env:MYSQL_HOST } else { "127.0.0.1" }
$MYSQL_PORT = if ($env:MYSQL_PORT) { $env:MYSQL_PORT } else { "3306" }
$MYSQL_USER = if ($env:MYSQL_USER) { $env:MYSQL_USER } else { "root" }
$MYSQL_PASSWORD = if ($env:MYSQL_PASSWORD) { $env:MYSQL_PASSWORD } else { "123456" }
$MYSQL_DB = "ztb_clean"

function Write-Step {
    param([string]$Message)
    Write-Host "`n============================================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Invoke-MySQL {
    param([string]$Query)
    $escapedQuery = $Query -replace '"', '\"'
    docker exec ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" -e "$Query" 2>$null
}

function Test-MySQLReady {
    $maxRetries = 30
    for ($i = 1; $i -le $maxRetries; $i++) {
        $result = docker exec ztb_mysql mysqladmin ping -u$MYSQL_USER -p"$MYSQL_PASSWORD" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] MySQL 已就绪 (尝试 $i/$maxRetries)" -ForegroundColor Green
            return $true
        }
        Write-Host "等待 MySQL 启动... ($i/$maxRetries)"
        Start-Sleep -Seconds 2
    }
    Write-Host "[FAIL] MySQL 启动超时" -ForegroundColor Red
    return $false
}

# ═══════════════════════════════════════════════════════
# 1. 启动 MySQL 容器
# ═══════════════════════════════════════════════════════
function Start-MySQLContainer {
    Write-Step "启动 MySQL Docker 容器"
    
    $existingContainer = docker ps -a --filter "name=ztb_mysql" --format "{{.Names}}" 2>$null
    if ($existingContainer -eq "ztb_mysql") {
        $isRunning = docker ps --filter "name=ztb_mysql" --format "{{.Names}}" 2>$null
        if ($isRunning -eq "ztb_mysql") {
            Write-Host "[INFO] 容器 ztb_mysql 已在运行" -ForegroundColor Yellow
            return
        }
        Write-Host "[INFO] 启动已存在的容器 ztb_mysql ..."
        docker start ztb_mysql
    } else {
        Write-Host "[INFO] 创建并启动新容器 ..."
        docker compose -f "$ComposeFile" up -d
    }
    
    if (-not (Test-MySQLReady)) {
        throw "MySQL 容器启动失败"
    }
}

# ═══════════════════════════════════════════════════════
# 2. 执行 DDL 建表
# ═══════════════════════════════════════════════════════
function Invoke-DDLSchema {
    Write-Step "执行 DDL 建表脚本"
    
    $schemaFile = Join-Path $ProjectDir "docker\mysql\init\01-schema.sql"
    if (-not (Test-Path $schemaFile)) {
        Write-Host "[WARN] schema.sql 未找到，跳过: $schemaFile" -ForegroundColor Yellow
        return
    }
    
    Write-Host "执行 schema.sql ..."
    Get-Content $schemaFile | docker exec -i ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" $MYSQL_DB
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] DDL 建表完成" -ForegroundColor Green
    } else {
        throw "DDL 执行失败"
    }
}

# ═══════════════════════════════════════════════════════
# 3. 导入 CSV 数据
# ═══════════════════════════════════════════════════════
function Import-CSVData {
    Write-Step "导入 CSV 数据到 ztb_clean"
    
    $csvScript = Join-Path $ProjectDir "scripts\csv_to_mysql.py"
    $csvDir = Join-Path $ProjectDir "raw_tables"
    
    if (-not (Test-Path $csvScript)) {
        Write-Host "[WARN] csv_to_mysql.py 未找到: $csvScript" -ForegroundColor Yellow
        return
    }
    
    if (-not (Test-Path $csvDir)) {
        Write-Host "[WARN] CSV 目录未找到: $csvDir" -ForegroundColor Yellow
        return
    }
    
    $truncateFlag = if ($CleanDeploy) { "--truncate" } else { "" }
    
    Write-Host "运行 csv_to_mysql.py ..."
    python "$csvScript" --csv-dir "$csvDir" --batch-size 5000 $truncateFlag
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 数据导入完成" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 数据导入可能有错误，请检查日志" -ForegroundColor Yellow
    }
}

# ═══════════════════════════════════════════════════════
# 4. 构建 FULLTEXT 索引
# ═══════════════════════════════════════════════════════
function Build-FulltextIndexes {
    Write-Step "构建 FULLTEXT 索引（ngram 分词器）"
    
    $fulltextDDL = @"
ALTER TABLE `company_info`
  ADD FULLTEXT INDEX `ft_company_info` (`company_name`, `business_scope`, `industry`, `address`) WITH PARSER ngram;
ALTER TABLE `company_penalty`
  ADD FULLTEXT INDEX `ft_penalty` (`company_name`, `illegal_behavior`, `penalty_result`) WITH PARSER ngram;
ALTER TABLE `product_info`
  ADD FULLTEXT INDEX `ft_product` (`product_name`, `supplier_name`, `product_parameters`, `category`) WITH PARSER ngram;
ALTER TABLE `bid_project`
  ADD FULLTEXT INDEX `ft_bid_project` (`project_name`, `purchaser`, `successful_bidder`, `subject_matter`) WITH PARSER ngram;
"@
    
    # 检查 FULLTEXT 索引是否已存在
    $existing = docker exec ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" -e "SHOW INDEX FROM ztb_clean.company_info WHERE Index_type='FULLTEXT';" 2>$null
    if ($existing -match "ft_company_info") {
        Write-Host "[INFO] FULLTEXT 索引已存在，跳过" -ForegroundColor Yellow
        return
    }
    
    Write-Host "创建 FULLTEXT 索引（大表可能需要几分钟）..."
    $fulltextDDL | docker exec -i ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" $MYSQL_DB
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] FULLTEXT 索引构建完成" -ForegroundColor Green
    } else {
        Write-Host "[WARN] FULLTEXT 索引构建可能失败（ngram 解析器不可用？请检查 MySQL 版本 >= 5.7）" -ForegroundColor Yellow
    }
}

# ═══════════════════════════════════════════════════════
# 5. 数据完整性验证
# ═══════════════════════════════════════════════════════
function Test-DataIntegrity {
    Write-Step "数据完整性验证"
    
    $tables = @("company_info", "company_penalty", "product_info", "bid_project")
    $allOk = $true
    
    foreach ($table in $tables) {
        $result = docker exec ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" -N -e "SELECT COUNT(*), ROUND(SUM(DATA_LENGTH + INDEX_LENGTH)/1024/1024, 2) FROM information_schema.TABLES WHERE TABLE_SCHEMA='ztb_clean' AND TABLE_NAME='$table';" 2>$null
        
        if ($result) {
            $parts = $result -split "\s+"
            $count = $parts[0].Trim()
            $size = $parts[1].Trim()
            Write-Host "  $table : $count 行, ${size}MB" -ForegroundColor $(if ([int]$count -gt 0) { "Green" } else { "Red" })
            if ([int]$count -eq 0) { $allOk = $false }
        } else {
            Write-Host "  $table : 查询失败" -ForegroundColor Red
            $allOk = $false
        }
    }
    
    if ($allOk) {
        Write-Host "`n[OK] 数据完整性验证通过" -ForegroundColor Green
    } else {
        Write-Host "`n[FAIL] 部分表数据为空，请检查导入日志" -ForegroundColor Red
    }
}

# ═══════════════════════════════════════════════════════
# 6. 连接测试
# ═══════════════════════════════════════════════════════
function Test-Connectivity {
    Write-Step "连通性测试"
    
    # 测试 MySQL 连接
    $testResult = docker exec ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" -e "SELECT 'MySQL_OK' AS status;" 2>$null
    if ($testResult -match "MySQL_OK") {
        Write-Host "[OK] MySQL 连接正常" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] MySQL 连接失败" -ForegroundColor Red
    }
    
    # 测试数据库选择
    $dbResult = docker exec ztb_mysql mysql -u$MYSQL_USER -p"$MYSQL_PASSWORD" -e "USE ztb_clean; SELECT DATABASE();" 2>$null
    if ($dbResult -match "ztb_clean") {
        Write-Host "[OK] 数据库 ztb_clean 可访问" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] 数据库 ztb_clean 不可访问" -ForegroundColor Red
    }
}

# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════
function Main {
    Write-Host "`n  ztb_clean 数据库部署工具" -ForegroundColor Magenta
    Write-Host "  项目目录: $ProjectDir" -ForegroundColor Gray
    Write-Host "  Compose:  $ComposeFile" -ForegroundColor Gray
    Write-Host "  目标:     MySQL 8.0 @ ${MYSQL_HOST}:${MYSQL_PORT}" -ForegroundColor Gray

    if ($VerifyOnly) {
        Test-Connectivity
        Test-DataIntegrity
        return
    }
    
    if ($ImportOnly) {
        Import-CSVData
        Build-FulltextIndexes
        Test-DataIntegrity
        return
    }
    
    # ── 完整部署流程 ──
    Start-MySQLContainer
    
    if (-not $InitOnly) {
        Invoke-DDLSchema
        Import-CSVData
        Build-FulltextIndexes
    }
    
    Test-Connectivity
    Test-DataIntegrity
    
    Write-Step "部署完成"
    Write-Host "  连接信息:"
    Write-Host "    Host:     $MYSQL_HOST" -ForegroundColor White
    Write-Host "    Port:     $MYSQL_PORT" -ForegroundColor White
    Write-Host "    User:     $MYSQL_USER" -ForegroundColor White
    Write-Host "    Database: $MYSQL_DB" -ForegroundColor White
    Write-Host "`n  常用命令:"
    Write-Host "    docker logs ztb_mysql              # 查看日志" -ForegroundColor Gray
    Write-Host "    docker exec -it ztb_mysql bash     # 进入容器" -ForegroundColor Gray
    Write-Host "    docker compose -f `"$ComposeFile`" down   # 停止并删除容器" -ForegroundColor Gray
}

Main
