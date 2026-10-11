# Live API walkthrough. Start the server first:  python manage.py runserver
# Then in another terminal:  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
# Billing is per real minute, so the script waits ~2 minutes during the session.

$B = "http://127.0.0.1:8000/api"
$sfx = Get-Random
function Show($label, $obj) { Write-Host "`n== $label" -ForegroundColor Cyan; $obj | ConvertTo-Json -Compress | Write-Host }
function Try-Post($url, $headers) {
    try { Invoke-RestMethod -Method Post $url -Headers $headers }
    catch { [pscustomobject]@{ http_status = $_.Exception.Response.StatusCode.value__; body = $_.ErrorDetails.Message } }
}

$userResp = Invoke-RestMethod -Method Post "$B/auth/register/" -ContentType "application/json" `
    -Body (@{ username = "alice$sfx"; password = "pass12345"; role = "USER" } | ConvertTo-Json)
$provResp = Invoke-RestMethod -Method Post "$B/auth/register/" -ContentType "application/json" `
    -Body (@{ username = "drbob$sfx"; password = "pass12345"; role = "PROVIDER"; per_minute_rate = "50" } | ConvertTo-Json)
$UserH = @{ Authorization = "Token $($userResp.token)" }
$ProvH = @{ Authorization = "Token $($provResp.token)" }
$HookH = @{ "X-Webhook-Secret" = "dev-webhook-secret" }

# 1. Insufficient balance: 200 < 5 x 50
$r1 = Invoke-RestMethod -Method Post "$B/wallet/recharges/" -Headers $UserH -Body @{ amount = 200 }
Invoke-RestMethod -Method Post "$B/wallet/recharges/callback/" -Headers $HookH -Body @{ reference = $r1.reference; status = "success" } | Out-Null
$s1 = Invoke-RestMethod -Method Post "$B/sessions/" -Headers $UserH -Body @{ provider_id = $provResp.user.id }
Invoke-RestMethod -Method Post "$B/sessions/$($s1.id)/accept/" -Headers $ProvH | Out-Null
Show "Start with balance 200 (needs 250) -> expect 400" (Try-Post "$B/sessions/$($s1.id)/start/" $UserH)

# 5. Duplicate recharge callback
$r2 = Invoke-RestMethod -Method Post "$B/wallet/recharges/" -Headers $UserH -Body @{ amount = 90 }
Show "Callback #1" (Invoke-RestMethod -Method Post "$B/wallet/recharges/callback/" -Headers $HookH -Body @{ reference = $r2.reference; status = "success" } | Select-Object applied, duplicate)
Show "Callback #2 (same) -> duplicate" (Invoke-RestMethod -Method Post "$B/wallet/recharges/callback/" -Headers $HookH -Body @{ reference = $r2.reference; status = "success" } | Select-Object applied, duplicate)
Show "Balance -> expect 290, not 380" (Invoke-RestMethod "$B/wallet/" -Headers $UserH)

# 4. Invalid transition
$s2 = Invoke-RestMethod -Method Post "$B/sessions/" -Headers $UserH -Body @{ provider_id = $provResp.user.id }
Show "End before start -> expect 400" (Try-Post "$B/sessions/$($s2.id)/end/" $UserH)

# 2 + 6. Happy path with real billing
Invoke-RestMethod -Method Post "$B/sessions/$($s2.id)/accept/" -Headers $ProvH | Out-Null
Show "Start" (Invoke-RestMethod -Method Post "$B/sessions/$($s2.id)/start/" -Headers $UserH | Select-Object status, per_minute_cost)
Write-Host "`nWaiting 125 seconds so 2 minutes become billable..." -ForegroundColor Yellow
Start-Sleep -Seconds 125
Show "Bill" (Invoke-RestMethod -Method Post "$B/sessions/$($s2.id)/bill/" -Headers $UserH | Select-Object billed_minutes, billed_amount)
Show "Bill again (same minute) -> still 2 minutes" (Invoke-RestMethod -Method Post "$B/sessions/$($s2.id)/bill/" -Headers $UserH | Select-Object billed_minutes, billed_amount)
Show "End" (Invoke-RestMethod -Method Post "$B/sessions/$($s2.id)/end/" -Headers $UserH | Select-Object status, ended_by, billed_minutes, talk_time_seconds)
Show "Balance -> expect 190 (290 - 2 x 50)" (Invoke-RestMethod "$B/wallet/" -Headers $UserH)
