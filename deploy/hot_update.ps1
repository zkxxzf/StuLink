<#
.SYNOPSIS
  StuLink NAS 增量热更新：只把 git 变更的文件经 SSH 上传并送进容器，省去每次打包 tar。

.DESCRIPTION
  tar 只是传输介质，可以省掉；但容器 /app **不是**宿主机挂载目录
  （只有 stulink-data → /app/data 是 bind），因此不能只 scp 到宿主机就生效，
  中间必须有一句 docker cp。完整链路：

      git diff 取变更 → 本地暂存 → scp 上传到 NAS _hot → docker cp 进容器
      →（可选）重启 → 验收 → 记录已部署提交

  与 deploy/nas_update_*.sh（整包发布）的区别：
    · 本脚本只处理**已提交的增量**，适合日常小改动快速上线；
    · 涉及新增文件、依赖变更或数据库迁移时，仍建议走整包脚本。

  注意：
    1. 上传前请自行保证工作区改动已 commit（脚本按 commit 差异取文件）；
    2. 删除的文件默认只告警不处理，加 -Delete 才会在容器内删除；
    3. 需要跑迁移的版本，请用 deploy/nas_update_*.sh（它会在停容器后按序执行）。

.PARAMETER From
  起始提交。缺省时读取 NAS 上 $DIR/.deployed_commit（由整包脚本与上一次本脚本写入）。

.PARAMETER NoRestart
  只更新代码不重启容器（默认会重启）。

.PARAMETER Delete
  对本次被删除的文件，在容器内一并 rm。

.PARAMETER DryRun
  只列出将要上传的文件，不执行任何远端操作。

.EXAMPLE
  powershell -File deploy\hot_update.ps1 -DryRun
  powershell -File deploy\hot_update.ps1
  powershell -File deploy\hot_update.ps1 -From 5c9306d
#>
param(
    [string]$From = '',
    [switch]$NoRestart,
    [switch]$Delete,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# git 输出按 UTF-8 解码：中文 Windows 上 PS5 默认按 GBK 解，含中文的路径会乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$NAS = '17752560383@10.193.191.210'
$DIR = '/volume1/docker/stulink'
$CT = 'stulink'

$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo '.git'))) { throw "不是 git 仓库：$repo" }

function Invoke-Remote([string[]]$Lines) {
    # 关键：不能把脚本文本直接管道给 ssh（PowerShell 5 的管道会引入 BOM/CR、
    # 并把非 ASCII 变成 '?'）。改为写临时文件（UTF-8 无 BOM + LF），再用标准输入重定向。
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        $script = ($Lines -join "`n") + "`n"
        [System.IO.File]::WriteAllText($tmp, $script, (New-Object System.Text.UTF8Encoding($false)))
        $p = Start-Process -FilePath 'ssh' -NoNewWindow -Wait -PassThru `
            -RedirectStandardInput $tmp `
            -ArgumentList @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', $NAS, 'bash -s')
        if ($p.ExitCode -ne 0) { Write-Warning "远端脚本退出码 $($p.ExitCode)" }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Overview {
    Write-Host ''
    Write-Host '  容器状态 / 版本 / 页面：'
    Invoke-Remote @(
        "docker inspect $CT --format '    State={{.State.Status}} Health={{.State.Health.Status}}'",
        "docker exec $CT grep -m1 'StuLink v' /app/config.py",
        "curl -s -o /dev/null -w '    /login -> %{http_code}\n' http://127.0.0.1/login"
    )
}

# ── 1. 确定起始提交 ──────────────────────────────────────────────────────
if (-not $From) {
    $From = ssh -o BatchMode=yes -o ConnectTimeout=15 $NAS "cat $DIR/.deployed_commit" 2>$null
    if ($From) { $From = ($From | Select-Object -First 1).Trim() }
}
if (-not $From) {
    throw "无法确定起始提交。请指定 -From <sha>，或先由整包脚本写入 $DIR/.deployed_commit"
}
git -C $repo cat-file -e "$From^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) { throw "本地缺少提交 $From（先 git fetch）" }

$head = (git -C $repo rev-parse HEAD).Trim()
if ($From -eq $head) {
    Write-Host "已经是最新：本地 HEAD 与已部署提交一致（$head）"
    exit 0
}

# ── 2. 取变更清单 ────────────────────────────────────────────────────────
# core.quotepath=false：否则 git 会把非 ASCII 路径转义成 "docs/\351\203\250..." 导致取不到文件
$raw = git -C $repo -c core.quotepath=false diff --name-status "$From..$head"
$changed = @(); $deleted = @()
foreach ($line in $raw) {
    if (-not $line) { continue }
    $parts = $line -split "`t"
    if ($parts.Count -lt 2) { continue }
    $st = $parts[0]; $path = $parts[-1]
    if ($st -eq 'D') { $deleted += $path } else { $changed += $path }
}

Write-Host "起始提交 : $From"
Write-Host "目标提交 : $head"
Write-Host "变更文件 : $($changed.Count) 个（新增/修改）"
foreach ($f in $changed) { Write-Host "    M  $f" }
if ($deleted.Count) {
    $hint = if ($Delete) { '' } else { '（默认不处理，加 -Delete 才会在容器内删除）' }
    Write-Host "删除文件 : $($deleted.Count) 个$hint"
    foreach ($f in $deleted) { Write-Host "    D  $f" }
}
if ($changed.Count -eq 0 -and $deleted.Count -eq 0) {
    Write-Host '没有需要上传的变更。'
    exit 0
}
if ($DryRun) { Write-Host '（DryRun：未执行任何远端操作）'; exit 0 }

# ── 3. 本地暂存（保持相对路径） ──────────────────────────────────────────
$stage = Join-Path $env:TEMP ('stulink_hot_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
foreach ($f in $changed) {
    $src = Join-Path $repo $f
    if (-not (Test-Path $src)) { Write-Warning "本地缺少文件，跳过：$f"; continue }
    $dst = Join-Path $stage $f
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item $src $dst -Force
}

# ── 4. 上传到 NAS 暂存目录 ───────────────────────────────────────────────
$stageName = Split-Path $stage -Leaf
Invoke-Remote @("rm -rf $DIR/_hot", "mkdir -p $DIR/_hot") | Out-Null
Write-Host "上传 $stageName …"
scp -O -r -o BatchMode=yes -o ConnectTimeout=20 $stage "${NAS}:$DIR/_hot/" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'scp 上传失败' }
Write-Host '    上传完成'

# ── 5. 送进容器 + 重启 + 验收 + 记录 ─────────────────────────────────────
$remote = @(
    'set -u',
    "echo '  -- 校验上传落点 --'",
    "find $DIR/_hot -type f | wc -l | sed 's/^/    收到文件数: /'",
    "echo '  -- docker cp 进容器 /app --'",
    "docker cp $DIR/_hot/$stageName/. ${CT}:/app/ && echo '    docker cp OK'"
)
if ($Delete -and $deleted.Count -gt 0) {
    $remote += "echo '  -- 删除容器内已移除文件 --'"
    foreach ($f in $deleted) {
        $remote += "docker exec $CT rm -f /app/$f && echo '    rm /app/$f'"
    }
}
if (-not $NoRestart) {
    $remote += "echo '  -- 重启容器 --'"
    $remote += "docker restart $CT >/dev/null && echo '    restarted'"
    $remote += 'for i in $(seq 1 40); do'
    $remote += '  code=$(curl -s -o /dev/null -w ''%{http_code}'' http://127.0.0.1/login || true)'
    $remote += '  if [ "$code" = "200" ]; then echo "    就绪 HTTP $code"; break; fi'
    $remote += '  sleep 3'
    $remote += 'done'
}
$remote += "printf '%s\n' $head > $DIR/.deployed_commit"
$remote += "rm -rf $DIR/_hot"
$remote += "echo '  -- 验收 --'"
$remote += "docker inspect $CT --format '    State={{.State.Status}} Health={{.State.Health.Status}}'"
$remote += "docker exec $CT grep -m1 'StuLink v' /app/config.py"
$remote += "curl -s -o /dev/null -w '    /login -> %{http_code}\n' http://127.0.0.1/login"

Invoke-Remote $remote

Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "增量更新完成：$From -> $head"
if (-not $NoRestart) { Invoke-Overview }
