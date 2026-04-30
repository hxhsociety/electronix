# ElectroniX Launcher — starts a local file server and opens the app
$port = 5173
$pub  = Join-Path $PSScriptRoot 'public'
Start-Process "http://localhost:$port"
Write-Host "ElectroniX running at http://localhost:$port"
Write-Host "Press Ctrl-C to quit."
# Simple static server via .NET HttpListener
$http = [System.Net.HttpListener]::new()
$http.Prefixes.Add("http://localhost:${port}/")
$http.Start()
while ($http.IsListening) {
    $ctx  = $http.GetContext()
    $req  = $ctx.Request
    $resp = $ctx.Response
    $path = $req.Url.LocalPath.TrimStart('/')
    if ($path -eq '') { $path = 'index.html' }
    $file = Join-Path $pub $path
    if (Test-Path $file -PathType Leaf) {
        $bytes = [System.IO.File]::ReadAllBytes($file)
        $ext   = [System.IO.Path]::GetExtension($file).ToLower()
        $mime  = switch ($ext) {
            '.html' { 'text/html' }; '.js' { 'application/javascript' }
            '.css'  { 'text/css' };  '.wasm'{ 'application/wasm' }
            '.glb'  { 'model/gltf-binary' }; '.json' { 'application/json' }
            '.csv'  { 'text/csv' }; '.svg' { 'image/svg+xml' }
            default { 'application/octet-stream' }
        }
        $resp.ContentType   = $mime
        $resp.ContentLength64 = $bytes.Length
        $resp.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $resp.StatusCode = 404
    }
    $resp.Close()
}
