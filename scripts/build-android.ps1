param([switch]$AcceptSdkLicense,[string]$ServerUrl='',[string]$JavaHome='',[string]$GradlePath='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskProject=Join-Path $taskRoot 'mobile\android'
$taskTools=Join-Path $taskProject '.local'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
if (!$AcceptSdkLicense) { throw 'Read https://developer.android.com/studio#downloads, then use -AcceptSdkLicense only if you explicitly agree to the Android SDK license.' }
if (!$JavaHome) {
    $taskJavaCommand=Get-Command java.exe -ErrorAction SilentlyContinue
    if ($taskJavaCommand) {
        $taskJavaDetails=& $taskJavaCommand.Source -XshowSettings:properties -version 2>&1 | ForEach-Object { $_.ToString() }
        $taskJavaMatch=$taskJavaDetails | Where-Object {$_ -match '^\s*java.home\s*='} | Select-Object -First 1
        $taskJavaVersion=$taskJavaDetails | Where-Object {$_ -match 'version "(\d+)'} | Select-Object -First 1
        if ($taskJavaMatch -and $taskJavaVersion -match 'version "(\d+)') {
            $taskJavaMajor=[int]$Matches[1]
            if ($taskJavaMajor -ge 17 -and $taskJavaMajor -le 25) { $JavaHome=($taskJavaMatch -split '=',2)[1].Trim() }
        }
    }
}
if (!$GradlePath) {
    $taskCandidates=@((Join-Path $taskTools 'gradle\gradle-9.3.1\bin\gradle.bat'))
    $taskDistributions=Join-Path $env:USERPROFILE '.gradle\wrapper\dists'
    if (Test-Path -LiteralPath $taskDistributions) {
        foreach ($taskDistribution in Get-ChildItem -LiteralPath $taskDistributions -Directory -Filter 'gradle-9.3.1-*') {
            foreach ($taskHashDirectory in Get-ChildItem -LiteralPath $taskDistribution.FullName -Directory) {
                $taskCandidates+=Join-Path $taskHashDirectory.FullName 'gradle-9.3.1\bin\gradle.bat'
            }
        }
    }
    $GradlePath=$taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
$taskUseExisting=$JavaHome -and $GradlePath
if ($taskUseExisting) {
    if (!(Test-Path -LiteralPath (Join-Path $JavaHome 'bin\javac.exe')) -or !(Test-Path -LiteralPath $GradlePath)) {throw 'Existing JDK/Gradle paths are invalid.'}
    & $taskPython (Join-Path $PSScriptRoot 'prepare-android-tools.py') --accept-sdk-license --sdk-only
} else { & $taskPython (Join-Path $PSScriptRoot 'prepare-android-tools.py') --accept-sdk-license }
if ($LASTEXITCODE -ne 0) { throw 'Android tool preparation failed.' }
if ($taskUseExisting) {$env:JAVA_HOME=$JavaHome} else {
    $taskJdk=Get-ChildItem -LiteralPath (Join-Path $taskTools 'jdk') -Directory | Select-Object -First 1
    $env:JAVA_HOME=$taskJdk.FullName
}
$env:ANDROID_HOME=Join-Path $taskTools 'sdk'
$env:ANDROID_USER_HOME=Join-Path $taskTools 'android-user'
$env:GRADLE_USER_HOME=Join-Path $taskTools 'gradle-home'
$taskReadCache=Join-Path $env:USERPROFILE '.gradle\caches'
if (Test-Path -LiteralPath (Join-Path $taskReadCache 'modules-2')) { $env:GRADLE_RO_DEP_CACHE=$taskReadCache }
$taskManager=Join-Path $env:ANDROID_HOME 'cmdline-tools\latest\bin\sdkmanager.bat'
# Accept only the licenses needed by the explicitly requested packages.
1..8 | ForEach-Object { 'y' } | & $taskManager "--sdk_root=$env:ANDROID_HOME" 'platforms;android-35' 'build-tools;36.0.0' 'platform-tools'
if ($LASTEXITCODE -ne 0) { throw 'SDK package installation failed.' }
$taskKeystore=Join-Path $taskTools 'debug.keystore'
if (!(Test-Path -LiteralPath $taskKeystore)) {
    & (Join-Path $env:JAVA_HOME 'bin\keytool.exe') -genkeypair -keystore $taskKeystore -storepass android -alias androiddebugkey -keypass android -dname 'CN=DevFlow Local Debug,O=DevFlow,C=CN' -keyalg RSA -keysize 2048 -validity 10000
    if ($LASTEXITCODE -ne 0) { throw 'Local debug signing key creation failed.' }
}
Push-Location $taskProject
try {
    $taskGradle=if($taskUseExisting){$GradlePath}else{Join-Path $taskTools 'gradle\gradle-9.3.1\bin\gradle.bat'}
    & $taskGradle --no-daemon --console=plain ":app:assembleDebug" ":app:lintDebug" "-PdevflowServerUrl=$ServerUrl"
    if ($LASTEXITCODE -ne 0) { throw 'APK build or Android lint failed.' }
    $taskDelivery=Join-Path $taskRoot 'artifacts\mobile'
    New-Item -ItemType Directory -Force -Path $taskDelivery | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskProject 'app\build\outputs\apk\debug\app-debug.apk') -Destination (Join-Path $taskDelivery 'DevFlow-0.18-debug.apk')
    & (Join-Path $env:ANDROID_HOME 'build-tools\36.0.0\apksigner.bat') verify --verbose --print-certs (Join-Path $taskDelivery 'DevFlow-0.18-debug.apk')
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    $taskDownloads=Join-Path $taskRoot 'frontend\public\downloads'
    New-Item -ItemType Directory -Force -Path $taskDownloads | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskDelivery 'DevFlow-0.18-debug.apk') -Destination (Join-Path $taskDownloads 'DevFlow-0.18-debug.apk')
} finally { Pop-Location }
