$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetFolder = Join-Path $PSScriptRoot 'assets'
New-Item -ItemType Directory -Force -Path $assetFolder | Out-Null
$sizes = @(16, 24, 32, 48, 64, 128, 256)
$images = [Collections.Generic.List[byte[]]]::new()
function Remove-PngColorProfile([byte[]]$Bytes) {
    $clean = [IO.MemoryStream]::new()
    $clean.Write($Bytes, 0, 8)
    $position = 8
    while ($position -lt $Bytes.Length) {
        $length = [int]$Bytes[$position] * 16777216 + [int]$Bytes[$position+1] * 65536 + [int]$Bytes[$position+2] * 256 + [int]$Bytes[$position+3]
        $type = [Text.Encoding]::ASCII.GetString($Bytes, $position + 4, 4)
        if ($type -ne 'iCCP') { $clean.Write($Bytes, $position, $length + 12) }
        $position += $length + 12
    }
    $result = $clean.ToArray()
    $clean.Dispose()
    return ,$result
}
foreach ($size in $sizes) {
    $bitmap = [Drawing.Bitmap]::new($size, $size)
    $g = [Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([Drawing.Color]::Transparent)
    $g.ScaleTransform($size / 256.0, $size / 256.0)
    $background = [Drawing.Drawing2D.GraphicsPath]::new()
    $background.AddArc(8, 8, 80, 80, 180, 90)
    $background.AddArc(168, 8, 80, 80, 270, 90)
    $background.AddArc(168, 168, 80, 80, 0, 90)
    $background.AddArc(8, 168, 80, 80, 90, 90)
    $background.CloseFigure()
    $green = [Drawing.Drawing2D.LinearGradientBrush]::new([Drawing.Rectangle]::new(8,8,240,240), [Drawing.ColorTranslator]::FromHtml('#338361'), [Drawing.ColorTranslator]::FromHtml('#1B533E'), 65.0)
    $g.FillPath($green, $background)
    $white = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#F6F8F7'))
    $capsule = [Drawing.Drawing2D.GraphicsPath]::new()
    $capsule.AddArc(96, 44, 64, 64, 180, 180)
    $capsule.AddArc(96, 92, 64, 64, 0, 180)
    $capsule.CloseFigure()
    $g.FillPath($white, $capsule)
    $pen = [Drawing.Pen]::new($white, 12)
    $pen.StartCap = $pen.EndCap = [Drawing.Drawing2D.LineCap]::Round
    $stand = [Drawing.Drawing2D.GraphicsPath]::new()
    $stand.AddLine(76, 116, 76, 132)
    $stand.AddBezier(76, 132, 76, 164, 98, 181, 128, 181)
    $stand.AddBezier(128, 181, 158, 181, 180, 164, 180, 132)
    $stand.AddLine(180, 132, 180, 116)
    $g.DrawPath($pen, $stand)
    $g.DrawLine($pen, 128, 181, 128, 209)
    $g.DrawLine($pen, 105, 209, 151, 209)
    $stream = [IO.MemoryStream]::new()
    $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
    $cleanPng = Remove-PngColorProfile $stream.ToArray()
    $images.Add($cleanPng)
    if ($size -eq 256) { [IO.File]::WriteAllBytes((Join-Path $assetFolder 'tingji.png'), $cleanPng) }
    $stream.Dispose(); $stand.Dispose(); $pen.Dispose(); $capsule.Dispose(); $white.Dispose(); $green.Dispose(); $background.Dispose(); $g.Dispose(); $bitmap.Dispose()
}
$file = [IO.File]::Create((Join-Path $assetFolder 'tingji.ico'))
$writer = [IO.BinaryWriter]::new($file)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $dimension = if ($sizes[$i] -eq 256) { 0 } else { $sizes[$i] }
        $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($bytes in $images) { $writer.Write($bytes) }
} finally { $writer.Dispose(); $file.Dispose() }
