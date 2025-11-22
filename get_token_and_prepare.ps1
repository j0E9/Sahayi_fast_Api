# get_token_and_prepare.ps1
param()

# ----- CONFIG -----
$BASE = "http://127.0.0.1:8000"
# fill these with the phone & password of a test user that exists in your DB
$PHONE = "<YOUR_PHONE_NUMBER>"
$PASSWORD = "<YOUR_PASSWORD>"
$BOOKING_ID = "<BOOKING_ID_TO_TEST>"

# ----- Try JSON login endpoints with multiple field names -----
$loginPaths = @("/auth/login", "/login", "/api/auth/login")
$phoneKeys = @("phone", "phone_number", "mobile", "username", "user")
$token = $null

Write-Host "Trying JSON login endpoints..."
foreach ($path in $loginPaths) {
    foreach ($phoneKey in $phoneKeys) {
        $body = @{}
        $body[$phoneKey] = $PHONE
        $body["password"] = $PASSWORD
        $json = $body | ConvertTo-Json
        try {
            $resp = Invoke-RestMethod -Method Post -Uri ($BASE + $path) -Body $json -Headers @{ "Content-Type" = "application/json" } -ErrorAction Stop
            Write-Host "Response from $path with key $phoneKey :"
            Write-Host ($resp | ConvertTo-Json -Depth 4)
            # common token fields
            if ($resp.access_token) { $token = $resp.access_token; break }
            if ($resp.token) { $token = $resp.token; break }
            if ($resp.data -and $resp.data.access_token) { $token = $resp.data.access_token; break }
        } catch {
            # ignore and continue
        }
    }
    if ($token) { break }
}

# ----- Try OAuth-style form login (/auth/token) -----
if (-not $token) {
    Write-Host "Trying form-encoded /auth/token..."
    try {
        $form = "grant_type=password&username=$($PHONE)&password=$($PASSWORD)"
        $resp2 = Invoke-RestMethod -Method Post -Uri ($BASE + "/auth/token") -Headers @{ "Content-Type" = "application/x-www-form-urlencoded" } -Body $form -ErrorAction Stop
        Write-Host "Response from /auth/token:"; Write-Host ($resp2 | ConvertTo-Json -Depth 4)
        if ($resp2.access_token) { $token = $resp2.access_token }
    } catch {
        # ignore
    }
}

if (-not $token) {
    Write-Error "Could not find access token. Try using the browser to log in, or paste your login route/payload shape here."
    exit 2
}

Write-Host "`n*** ACCESS TOKEN FOUND ***`n$token`n"

# ----- Use the access token to call /action/prepare -----
$headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
$body = @{ action = "issue_warning"; booking_id = $BOOKING_ID } | ConvertTo-Json

try {
    Write-Host "Calling /action/prepare..."
    $prep = Invoke-RestMethod -Method Post -Uri "$BASE/action/prepare" -Headers $headers -Body $body -ErrorAction Stop
    Write-Host "prepare response:"; Write-Host ($prep | ConvertTo-Json -Depth 4)
    $actionToken = $prep.action_token
    if (-not $actionToken) { Write-Error "No action_token in prepare response."; exit 3 }
    Write-Host "`nReceived action_token:`n$actionToken`n"

    # now call issue_warning (first time)
    Write-Host "Calling /action/issue_warning (first time)..."
    $h2 = @{ "x-action-token" = $actionToken; "Content-Type" = "application/json"; Authorization = "Bearer $token" }
    $body2 = @{ booking_id = $BOOKING_ID } | ConvertTo-Json
    $res1 = Invoke-RestMethod -Method Post -Uri "$BASE/action/issue_warning" -Headers $h2 -Body $body2 -ErrorAction Stop
    Write-Host "issue_warning (first):"; Write-Host ($res1 | ConvertTo-Json -Depth 4)

    # second call (replay)
    Write-Host "Calling /action/issue_warning (second time - should fail)..."
    try {
        $res2 = Invoke-RestMethod -Method Post -Uri "$BASE/action/issue_warning" -Headers $h2 -Body $body2 -ErrorAction Stop
        Write-Host "issue_warning (second) unexpectedly succeeded:"; Write-Host ($res2 | ConvertTo-Json -Depth 4)
    } catch {
        Write-Host "Expected failure on reuse. Error:"; Write-Host $_.Exception.Response.Content
    }
} catch {
    Write-Error "Error calling prepare/issue endpoints: $_"
    exit 4
}
