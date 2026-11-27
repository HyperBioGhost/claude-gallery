# Puts the Claude Gallery instructions into the user's Claude Code CLAUDE.md.
#
# Runs on every install, upgrades included: the block between the gallery
# markers is replaced, so an upgrade carries new rules. Everything else in the
# file is left exactly as it was. The text comes from claude-instructions.md,
# shipped beside this script -- the one copy, also what the README points
# other AI tools to.
#
# Parameters exist so a test can point the script at a scratch folder.
param(
    [string]$ClaudeMd = (Join-Path $env:USERPROFILE '.claude\CLAUDE.md'),
    [string]$Artifacts = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ClaudeGallery\artifacts'),
    [string]$Source = (Join-Path $PSScriptRoot 'claude-instructions.md')
)
$ErrorActionPreference = 'Stop'

New-Item -Path (Split-Path $ClaudeMd) -ItemType Directory -Force | Out-Null
New-Item -Path $Artifacts -ItemType Directory -Force | Out-Null

$existing = if (Test-Path $ClaudeMd) { [IO.File]::ReadAllText($ClaudeMd) } else { '' }
# Follow the file's own line endings, so the block does not mix them in.
$nl = if ($existing -match "`r`n") { "`r`n" } else { "`n" }
$body = ([IO.File]::ReadAllText($Source).Trim()) -replace "`r?`n", $nl
$block = "<!-- claude-gallery-start -->$nl$body$nl<!-- claude-gallery-end -->"

$pattern = '<!-- claude-gallery-start -->[\s\S]*?<!-- claude-gallery-end -->'
if ([regex]::IsMatch($existing, $pattern)) {
    # Replace the block where it is, so the person's own sections keep their
    # order and spacing; any extra copy from an old bug is dropped.
    $state = @{ first = $true }
    $text = [regex]::Replace($existing, $pattern, {
        param($m)
        if ($state.first) { $state.first = $false; $block } else { '' }
    })
} else {
    $kept = $existing.TrimEnd()
    $text = if ($kept) { $kept + $nl + $nl + $block + $nl } else { $block + $nl }
}

# UTF-8 without a byte-order mark, which Windows PowerShell 5.1 would add.
[IO.File]::WriteAllText($ClaudeMd, $text, (New-Object Text.UTF8Encoding($false)))
Write-Host "Claude Gallery instructions written to $ClaudeMd"
