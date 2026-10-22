<#
.SYNOPSIS
    StuLink → 豆包「自定义连接器」接入助手。

.DESCRIPTION
    做三件事，省掉照着文档手填/手算 base64 的功夫：
      1) 用给定账号密码生成 Authorization: Basic xxx（豆包自定义 Headers 只需这一个头）；
      2) 真实探活：向 MCP 端点发 MCP initialize + tools/list，把 HTTP 状态、
         返回的身份信息、工具清单打出来（连不通/密码错会明确告诉你是哪一类）；
      3) 打印豆包「新建自定义连接器」对话框里逐字段要填的内容，
         以及本机 STDIO 写法的现成 JSON（含绝对路径）。

.PARAMETER User
    StuLink 账号（建议用最小权限账号，别用 admin）。

.PARAMETER Password
    该账号密码。

.PARAMETER Url
    MCP 端点，默认 http://127.0.0.1:8765/mcp
      · 8765 = 本机 Docker 容器映射端口（deploy/docker-compose.yml）
      · 8766 = 直接跑 mcp_server\run.py 时 config.json 里的端口

.PARAMETER Json
    额外打印可直接粘进支持配置文件客户端（Cherry Studio / Claude Desktop /
    CodeBuddy .codebuddy/mcp.json 等）的 mcpServers JSON。

.PARAMETER Copy
    把 Basic 头复制到剪贴板（豆包对话框里直接 Ctrl+V）。

.PARAMETER SkipProbe
    只生成配置，不联网探活。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\mcp_doubao_connector.ps1 `
        -User teacher01 -Password 'YourPass' -Json -Copy

.EXAMPLE
    # 探活容器里的服务，并输出配置
    .\tools\mcp_doubao_connector.ps1 -User gradeleader01 -Password 'YourPass' `
        -Url http://127.0.0.1:8765/mcp
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$Url = 'http://127.0.0.1:8765/mcp',
    [switch]$Json,
    [switch]$Copy,
    [switch]$SkipProbe
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$Root = Split-Path -Parent $PSScriptRoot
$PyExe = Join-Path $Root 'mcp_server\.venv\Scripts\python.exe'
$StdioPy = Join-Path $Root 'mcp_server\run_stdio.py'

$basic = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${User}:${Password}"))

function Write-Head($t) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkGray
    Write-Host " $t" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkGray
}

Write-Head 'StuLink MCP → 豆包 自定义连接器'
Write-Host ("  端点    : {0}" -f $Url)
Write-Host ("  账号    : {0}" -f $User)
Write-Host ("  Basic 头: {0}" -f $basic) -ForegroundColor Green
if ($Copy) {
    try {
        Set-Clipboard -Value $basic
        Write-Host '  （已复制到剪贴板，豆包「自定义 Headers」里直接粘贴）' -ForegroundColor DarkGray
    } catch {
        Write-Host '  （复制剪贴板失败，请手工选中上面的值）' -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- 探活
$probeOk = $false
$sessionId = ''
$toolsLine = ''

if (-not $SkipProbe) {
    Write-Head '探活（MCP initialize + tools/list）'
    $initBody = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"stulink-doubao-probe","version":"1.0"}}}'
    $hdrs = @{
        Authorization  = $basic
        Accept         = 'application/json, text/event-stream'
        'Content-Type' = 'application/json'
    }
    $resp = $null
    $fail = ''
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method POST -Headers $hdrs -Body $initBody `
            -UseBasicParsing -TimeoutSec 20
    } catch {
        $r = $_.Exception.Response
        if ($r) {
            $code = [int]$r.StatusCode
            $text = ''
            # PS 5.1 把错误响应体放在 ErrorDetails.Message；拿不到再退回读流
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                $text = $_.ErrorDetails.Message
            } else {
                try {
                    $sr = New-Object IO.StreamReader($r.GetResponseStream())
                    $text = $sr.ReadToEnd()
                } catch { }
            }
            switch -Regex ($code) {
                '^401' {
                    if ($text -match 'invalid_credentials') {
                        $fail = '账号或密码不对（401 invalid_credentials）。'
                    } elseif ($text -match 'invalid_token') {
                        $fail = '服务端开了 require_token=true，需要额外带 X-StuLink-Token。'
                    } elseif ($text -match 'account_disabled') {
                        $fail = '账号已停用（401 account_disabled）。'
                    } else {
                        $fail = "缺少/不合法凭据（401）。返回：$text"
                    }
                }
                '^421' { $fail = 'Host 头不在白名单（421）：把域名/IP 加进 config.json 的 allowed_hosts 或 STULINK_MCP_ALLOWED_HOSTS。' }
                default { $fail = "HTTP $code。返回：$text" }
            }
        } else {
            $fail = "连不上：$($_.Exception.Message)`n         先确认服务在跑：mcp_server\start_mcp.bat（直跑）或 docker ps | findstr stulink-mcp"
        }
    }

    if ($resp) {
        $probeOk = $true
        Write-Host ("  HTTP {0} {1} —— 端点可达、凭据通过" -f [int]$resp.StatusCode, $resp.StatusDescription) -ForegroundColor Green
        try {
            $sessionId = [string]$resp.Headers['mcp-session-id']
        } catch { }
        if ($sessionId) {
            Write-Host ("  会话 id: {0}" -f $sessionId) -ForegroundColor DarkGray
            $listBody = '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
            $lh = @{
                Authorization        = $basic
                Accept               = 'application/json, text/event-stream'
                'Content-Type'       = 'application/json'
                'mcp-session-id'     = $sessionId
                'MCP-Protocol-Version' = '2025-06-18'
            }
            try {
                $lr = Invoke-WebRequest -Uri $Url -Method POST -Headers $lh -Body $listBody `
                    -UseBasicParsing -TimeoutSec 20
                $txt = $lr.Content
                # SSE 形态：从 event: message / data: {...} 里抠 JSON
                if ($txt -match 'data:\s*(\{.*\})') { $txt = $Matches[1] }
                $obj = $txt | ConvertFrom-Json
                $names = @($obj.result.tools | ForEach-Object { $_.name })
                $toolsLine = ($names -join '、')
                Write-Host ("  工具 {0} 个：{1}" -f $names.Count, $toolsLine) -ForegroundColor Green
                Write-Host '  可用 prompt 试一句：「用 StuLink 看看 2025 级这次月考的本科线上线率」' -ForegroundColor DarkGray
            } catch {
                Write-Host ("  tools/list 失败（不影响接入）：{0}" -f $_.Exception.Message) -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host ("  ✗ {0}" -f $fail) -ForegroundColor Red
    }
}

# ---------------------------------------------------------------- 豆包对话框要填的
Write-Head '豆包 →「技能 · 连接器 · 伙伴」→ 连接器 → 新建自定义连接器'
$loopback = $Url -match '://(127\.0\.0\.1|localhost|\[::1\])'
if ($loopback) {
    Write-Host '  ⚠ 端点写的是本机回环地址。豆包的 HTTP 连接器由云端发起请求，' -ForegroundColor Yellow
    Write-Host '    127.0.0.1 会指向豆包的服务器而不是你的电脑 → 必连不上。三个选法：' -ForegroundColor Yellow
    Write-Host '      A. 开隧道拿公网地址（最快验证）：' -ForegroundColor Yellow
    Write-Host '         cloudflared tunnel --url http://127.0.0.1:8765' -ForegroundColor White
    Write-Host '         拿到 https://xxx.trycloudflare.com 后，URL 填 …/mcp' -ForegroundColor DarkGray
    Write-Host '      B. 部署到公网（Nginx + HTTPS 反代 → https://域名/mcp），长期方案' -ForegroundColor Yellow
    Write-Host '      C. 传输类型改 STDIO（数据不出网，见下面第二段 JSON）' -ForegroundColor Yellow
}
Write-Host ''
Write-Host '  ┌ 服务器名称        StuLink' -ForegroundColor White
Write-Host '  ├ 传输类型          HTTP（若要让数据不出本机 → 选 STDIO）' -ForegroundColor White
Write-Host ("  ├ 服务器 URL        {0}" -f ($Url -replace '127\.0\.0\.1', '<公网地址或隧道域名>')) -ForegroundColor White
Write-Host '  └ 自定义 Headers    Authorization = ' -NoNewline -ForegroundColor White
Write-Host $basic -ForegroundColor Green
Write-Host ''
Write-Host '  保存后：「我安装的」里确认开关已开 → 任务框用 / 或 @ 唤起 StuLink。' -ForegroundColor DarkGray
Write-Host '  公网暴露前请过一遍 docs/规范与说明/AI工具安装与配置指南.md 的加固清单' -ForegroundColor DarkGray
Write-Host '  （HTTPS / require_token=true / allow_write=false / 专用最小权限账号 / allowed_hosts）。' -ForegroundColor DarkGray

# ---------------------------------------------------------------- 现成 JSON
if ($Json) {
    Write-Head '可直接粘贴的客户端配置（HTTP）'
    $httpCfg = [ordered]@{
        mcpServers = [ordered]@{
            stulink = [ordered]@{
                type    = 'streamableHttp'
                url     = $Url
                headers = [ordered]@{ Authorization = $basic }
            }
        }
    }
    $httpCfg | ConvertTo-Json -Depth 6

    Write-Head '可直接粘贴的客户端配置（STDIO · 本地子进程，数据不出网）'
    $stdioCfg = [ordered]@{
        mcpServers = [ordered]@{
            stulink = [ordered]@{
                type    = 'stdio'
                command = $PyExe
                args    = @($StdioPy)
                env     = [ordered]@{
                    PYTHONUTF8            = '1'
                    STULINK_MCP_USER      = $User
                    STULINK_MCP_PASSWORD  = $Password
                }
            }
        }
    }
    $stdioCfg | ConvertTo-Json -Depth 6
    Write-Host ("  （STDIO 需要 venv 已建好：{0}）" -f $PyExe) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- 结论
Write-Head '小结'
if ($SkipProbe) {
    Write-Host '  （已跳过探活）' -ForegroundColor DarkGray
} elseif ($probeOk) {
    Write-Host '  ✓ 服务可达 + 凭据有效，把上面的 URL 与 Headers 填进豆包即可。' -ForegroundColor Green
} else {
    Write-Host '  ✗ 探活未通过，先解决上面那条红字再填豆包，别在客户端里排查。' -ForegroundColor Red
    exit 1
}
