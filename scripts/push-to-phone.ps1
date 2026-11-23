#requires -Version 5.1
<#
.SYNOPSIS
  把本仓库推到手机上，并用手机上的 astrbot_plugin_execrelay 触发部署 + 自检。

.DESCRIPTION
  走的是两条已经验证过的通道：
    adb push  -> /sdcard/Android/data/io.astrbot.android/files/shared/bigfatfish-stack/
    exec.json -> astrbot_plugin_execrelay 在 AstrBot 的 Python 环境里执行命令

  前提：手机已开 USB 调试并 adb 认到设备；AstrBot 在跑且 execrelay 插件已启用。

.EXAMPLE
  .\scripts\push-to-phone.ps1 -AdbPath "D:\VIVO\pcsuite\adb_41\adb.exe" -Deploy
#>
param(
  [string]$AdbPath = "adb",
  [string]$SharedRoot = "/sdcard/Android/data/io.astrbot.android/files/shared",
  [switch]$Deploy,
  [switch]$Verify,
  [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RemoteDir = "$SharedRoot/bigfatfish-stack"

function Invoke-Adb {
  param([string[]]$Arguments)
  $out = & $AdbPath @Arguments 2>&1
  if ($LASTEXITCODE -ne 0) { throw "adb $($Arguments -join ' ') 失败: $out" }
  return $out
}

Write-Host "[1/3] 检查设备" -ForegroundColor Cyan
$devices = Invoke-Adb @('devices')
if ($devices -notmatch '\tdevice') { throw "没有已授权的 adb 设备：`n$devices" }
($devices | Select-String 'device') | ForEach-Object { Write-Host "      $_" }

Write-Host "[2/3] 推送仓库到 $RemoteDir" -ForegroundColor Cyan
Invoke-Adb @('shell', "rm -rf '$RemoteDir'") | Out-Null
Invoke-Adb @('shell', "mkdir -p '$RemoteDir'") | Out-Null
# 只推代码与配置，跳过 .git / 日志 / 缓存
$exclude = @('.git', 'node_modules', '__pycache__', '.venv')
$items = Get-ChildItem $RepoRoot -Force | Where-Object { $exclude -notcontains $_.Name }
foreach ($it in $items) {
  Invoke-Adb @('push', $it.FullName, "$RemoteDir/") | Out-Null
}
Write-Host "      已推送 $(($items | Measure-Object).Count) 个顶层条目" -ForegroundColor Green

function Invoke-PhoneExec {
  param([string]$Command, [int]$Timeout = 300)
  $seq = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  $tmp = Join-Path $env:TEMP "exec-$seq.json"
  $payload = @{ cmd = $Command; timeout = $Timeout; seq = $seq } | ConvertTo-Json -Compress
  [System.IO.File]::WriteAllText($tmp, $payload, (New-Object System.Text.UTF8Encoding($false)))
  Invoke-Adb @('push', $tmp, "$SharedRoot/exec.json") | Out-Null
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  $deadline = (Get-Date).AddSeconds($Timeout + 60)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    $raw = (& $AdbPath shell "cat $SharedRoot/exec_res.json" 2>$null) -join "`n"
    if (-not $raw) { continue }
    try { $res = $raw | ConvertFrom-Json } catch { continue }
    if ($res.seq -ne $seq) { continue }
    Write-Host "      rc=$($res.rc)" -ForegroundColor $(if ($res.rc -eq 0) { 'Green' } else { 'Red' })
    if ($res.out) { Write-Host $res.out }
    if ($res.err) { Write-Host $res.err -ForegroundColor Yellow }
    return $res
  }
  throw "等 exec_res 超时（seq=$seq）"
}

Write-Host "[3/3] 在手机里执行" -ForegroundColor Cyan
if ($Deploy) {
  # proot 里的 /android-share 就是上面的 shared 目录
  Invoke-PhoneExec -Command "cd /android-share/bigfatfish-stack && bash scripts/deploy-phone.sh" -Timeout $TimeoutSec | Out-Null
}
if ($Verify) {
  Invoke-PhoneExec -Command "cd /android-share/bigfatfish-stack && bash scripts/verify-phone.sh" -Timeout $TimeoutSec | Out-Null
}
if (-not $Deploy -and -not $Verify) {
  Write-Host "      只推送，没执行。加 -Deploy / -Verify 才会跑脚本。" -ForegroundColor Yellow
}
