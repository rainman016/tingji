param([switch]$InstallDesktop, [switch]$UpdateDesktop)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '找不到 Windows .NET Framework C# 编译器。' }
$desktopExe = Join-Path ([Environment]::GetFolderPath('Desktop')) '听记.exe'
$output = Join-Path $projectRoot '听记.exe'
if ($UpdateDesktop) { $InstallDesktop = $true }
if ($InstallDesktop -and (Test-Path -LiteralPath $desktopExe)) {
    if (-not $UpdateDesktop -or -not (Test-Path -LiteralPath $output) -or
        (Get-FileHash -LiteralPath $desktopExe).Hash -ne (Get-FileHash -LiteralPath $output).Hash) {
        throw '桌面已存在听记.exe，未覆盖。更新仅允许替换与项目原 EXE 完全一致的副本。'
    }
}
$temporary = Join-Path $projectRoot '.tmp'
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
$rootResource = Join-Path $temporary 'launcher-project-root.txt'
[IO.File]::WriteAllText($rootResource, $projectRoot, [Text.Encoding]::UTF8)
$icon = Join-Path $projectRoot 'assets\tingji.ico'
& $compiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/win32icon:$icon" "/resource:$rootResource,Tingji.ProjectRoot" "/out:$output" (Join-Path $projectRoot 'desktop_launcher.cs')
if ($LASTEXITCODE -ne 0) { throw '听记启动器编译失败。' }
if ($InstallDesktop) {
    [IO.File]::Copy($output, $desktopExe, [bool]$UpdateDesktop)
    if (-not ('TingjiIconRefresh' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class TingjiIconRefresh { [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern void SHChangeNotify(uint eventId, uint flags, string path, IntPtr other); }'
    }
    [TingjiIconRefresh]::SHChangeNotify(0x2000, 0x0005, $desktopExe, [IntPtr]::Zero)
    [TingjiIconRefresh]::SHChangeNotify(0x08000000, 0, $null, [IntPtr]::Zero)
}
Write-Output $output
if ($InstallDesktop) { Write-Output $desktopExe }
