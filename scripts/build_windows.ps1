$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$model = "models\translator.gguf"
$engine = "runtime\llama-cli.exe"
if (-not (Test-Path $model)) { throw "BLOCKED: модели GGUF нет. Не публикуйте установщик без неё." }
if (-not (Test-Path $engine)) { throw "BLOCKED: нет Windows llama-cli.exe. Не публикуйте установщик." }
if ((Get-Item $model).Length -lt 1GB) { throw "BLOCKED: модель подозрительно мала; проверьте точный файл и SHA256." }
if (([System.IO.File]::ReadAllBytes((Resolve-Path $model))[0..3] | ForEach-Object {[char]$_}) -join '' -ne 'GGUF') { throw "Некорректный заголовок GGUF." }
python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Ошибка установки build dependencies" }
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Тесты не пройдены" }
python -m PyInstaller --clean --noconfirm --windowed --onedir --name ArabicaAIPro --contents-directory . --collect-all docx --add-data "models\translator.gguf;models" --add-binary "runtime\llama-cli.exe;runtime" run_arabica.py
if ($LASTEXITCODE -ne 0) { throw "Ошибка PyInstaller" }
$compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $compiler)) { throw "BLOCKED: установите Inno Setup 6 НА СБОРОЧНОЙ МАШИНЕ." }
& $compiler "installer\ArabicaAIPro.iss"
if ($LASTEXITCODE -ne 0) { throw "Ошибка Inno Setup" }
Write-Host "Проверьте полный offline Windows 10 smoke и SHA256 до публикации релиза."
