"""Impressora virtual "Romaneio Freedom".

Cria uma impressora do Windows (driver "Microsoft Print To PDF") ligada a uma porta que é um ARQUIVO.
Ao imprimir o romaneio do sistema nessa impressora, o Windows grava o PDF ali e o programa lê. Uma tarefa
agendada abre o programa quando algo é impresso (evento 307 do log de impressão).

A pasta da porta fica em C:\\ProgramData\\RoteirizaFreedom\\entrada (caminho curto, sem espaços nem parênteses,
fora de Downloads/OneDrive, igual para todo mundo), assim a impressora não quebra se a pasta do programa mudar.
Precisa de permissão de administrador uma única vez (instalação).
"""
import ctypes
import getpass
import os
import subprocess
import sys
import tempfile
import time

import servidor

NOME = "Romaneio Freedom"
TAREFA = "RoteirizaFreedom-Romaneio"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def eh_exe():
    return bool(getattr(sys, "frozen", False))


def log_instalacao():
    return os.path.join(servidor.pasta_dados(), "instalacao_impressora.log")


def _ps(t):
    """Texto seguro dentro de aspas simples do PowerShell."""
    return str(t).replace("'", "''")


def _usuario():
    dom = os.environ.get("USERDOMAIN") or ""
    return f"{dom}\\{getpass.getuser()}" if dom else getpass.getuser()


def _powershell(comando, timeout=30):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True,
                       timeout=timeout, creationflags=SEM_JANELA)
    return (r.stdout or "") + (r.stderr or "")


def impressora_instalada():
    try:
        return "SIM" in _powershell(f"if (Get-Printer -Name '{_ps(NOME)}' -ErrorAction SilentlyContinue) {{ 'SIM' }}", 20)
    except Exception:
        return False


def enviar_teste():
    """Manda uma página de teste para a impressora (como um 'imprimir' qualquer). Devolve '' ou o erro."""
    try:
        return _powershell(f"'Teste Roteiriza Freedom' | Out-Printer -Name '{_ps(NOME)}'", 40).strip()
    except Exception as e:                                   # noqa: BLE001
        return f"ERRO: {e}"


def diagnostico():
    """Texto com o estado da impressora, da pasta, de arquivo sobrando e de documentos presos na fila."""
    n, pasta, arq = _ps(NOME), _ps(servidor.pasta_entrada()), _ps(servidor.porta_arquivo())
    cmd = f"""$p = Get-Printer -Name '{n}' -ErrorAction SilentlyContinue
if (-not $p) {{ 'IMPRESSORA: nao existe' }} else {{ 'IMPRESSORA: ' + $p.Name + ' | driver=' + $p.DriverName + ' | porta=' + $p.PortName + ' | status=' + $p.PrinterStatus }}
'PORTA ESPERADA: {arq}'
'PASTA existe: ' + (Test-Path -LiteralPath '{pasta}')
'ARQUIVO sobrando na pasta: ' + (Test-Path -LiteralPath '{arq}')
Get-PrintJob -PrinterName '{n}' -ErrorAction SilentlyContinue | ForEach-Object {{ 'JOB ' + $_.Id + ' | ' + $_.JobStatus + ' | ' + $_.TotalPages + ' pag' }}
'SPOOLER: ' + (Get-Service Spooler).Status"""
    try:
        return _powershell(cmd, 40).strip()
    except Exception as e:                                   # noqa: BLE001
        return f"ERRO ao diagnosticar: {e}"


def resumo_diagnostico(texto):
    """Traduz o diagnóstico numa frase simples."""
    t = texto or ""
    if "nao existe" in t:
        return "A impressora não está instalada."
    if "PASTA existe: False" in t:
        return "A pasta onde a impressora grava o PDF não existe."
    if "ARQUIVO sobrando na pasta: True" in t:
        return "Sobrou um romaneio.pdf na pasta e isso trava a próxima impressão."
    if "\nJOB " in t or t.startswith("JOB "):
        return "Tem documento preso na fila de impressão."
    if "SPOOLER: Stopped" in t:
        return "O serviço de impressão do Windows está parado."
    return "A impressora existe e a pasta está certa, mas o Windows não gravou o PDF."


def script_instalar(exe, usuario):
    return f"""$ErrorActionPreference = 'Stop'
$log = '{_ps(log_instalacao())}'
function Log($m) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 's') + ' ' + $m) -Encoding UTF8 }}
Set-Content -LiteralPath $log -Value '' -Encoding UTF8
try {{
  $nome = '{_ps(NOME)}'
  $entrada = '{_ps(servidor.pasta_entrada())}'
  $porta = '{_ps(servidor.porta_arquivo())}'
  New-Item -ItemType Directory -Force -Path $entrada | Out-Null
  & icacls.exe $entrada /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null
  Log 'pasta da impressora pronta (usuarios podem ler e apagar os PDFs)'
  $antiga = Get-Printer -Name $nome -ErrorAction SilentlyContinue
  if ($antiga) {{
    $portaAntiga = $antiga.PortName
    Remove-Printer -Name $nome
    Start-Sleep -Milliseconds 800
    if ($portaAntiga -and $portaAntiga -ne $porta) {{ Remove-PrinterPort -Name $portaAntiga -ErrorAction SilentlyContinue }}
    Log 'impressora antiga removida'
  }}
  if (-not (Get-PrinterPort -Name $porta -ErrorAction SilentlyContinue)) {{ Add-PrinterPort -Name $porta; Log 'porta criada' }}
  Add-Printer -Name $nome -DriverName 'Microsoft Print To PDF' -PortName $porta
  Log 'impressora criada'
  & wevtutil.exe sl Microsoft-Windows-PrintService/Operational /e:true
  Log 'log de impressao ativado'
  $acao = New-ScheduledTaskAction -Execute '{_ps(exe)}' -Argument '--romaneio'
  $cls = Get-CimClass -Namespace 'ROOT\\Microsoft\\Windows\\TaskScheduler' -ClassName 'MSFT_TaskEventTrigger'
  $gatilho = New-CimInstance -CimClass $cls -ClientOnly
  $gatilho.Enabled = $true
  $gatilho.Subscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-PrintService/Operational"><Select Path="Microsoft-Windows-PrintService/Operational">*[System[(EventID=307)]]</Select></Query></QueryList>'
  $principal = New-ScheduledTaskPrincipal -UserId '{_ps(usuario)}' -LogonType Interactive -RunLevel Limited
  $config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances Parallel -ExecutionTimeLimit (New-TimeSpan -Hours 12)
  Register-ScheduledTask -TaskName '{TAREFA}' -Action $acao -Trigger $gatilho -Principal $principal -Settings $config -Force | Out-Null
  Log 'tarefa criada'
  Log 'OK'
}} catch {{
  Log ('ERRO: ' + $_.Exception.Message)
}}
"""


def script_remover():
    return f"""$ErrorActionPreference = 'SilentlyContinue'
$log = '{_ps(log_instalacao())}'
function Log($m) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 's') + ' ' + $m) -Encoding UTF8 }}
Set-Content -LiteralPath $log -Value '' -Encoding UTF8
Unregister-ScheduledTask -TaskName '{TAREFA}' -Confirm:$false
Remove-Printer -Name '{_ps(NOME)}'
Remove-PrinterPort -Name '{_ps(servidor.porta_arquivo())}'
Log 'removido'
Log 'OK'
"""


def script_reparar():
    """Limpa a fila travada: para o serviço de impressão, apaga os documentos presos e o PDF que sobrou."""
    return f"""$ErrorActionPreference = 'SilentlyContinue'
$log = '{_ps(log_instalacao())}'
function Log($m) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 's') + ' ' + $m) -Encoding UTF8 }}
Set-Content -LiteralPath $log -Value '' -Encoding UTF8
Stop-Service -Name Spooler -Force
Start-Sleep -Seconds 2
Remove-Item -Path (Join-Path $env:windir 'System32\\spool\\PRINTERS\\*') -Force
Log 'fila de impressao limpa'
Remove-Item -LiteralPath '{_ps(servidor.porta_arquivo())}' -Force
Log 'PDF que sobrou apagado'
Start-Service -Name Spooler
Log 'servico de impressao reiniciado'
Log 'OK'
"""


def executa_como_admin(script, espera=90):
    """Roda o script PowerShell pedindo permissão de administrador (UAC). Devolve o texto do log."""
    ps1 = os.path.join(tempfile.gettempdir(), "roteiriza_impressora.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:   # BOM: o PowerShell lê os acentos certo
        f.write(script)
    log = log_instalacao()
    try:
        os.remove(log)
    except OSError:
        pass
    r = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "powershell.exe",
        f'-NoProfile -ExecutionPolicy Bypass -File "{ps1}"', None, 0)
    if r <= 32:
        return "ERRO: a permissão de administrador foi negada ou cancelada."
    fim = time.time() + espera
    texto = ""
    while time.time() < fim:
        time.sleep(0.7)
        try:
            with open(log, encoding="utf-8-sig") as f:
                texto = f.read()
        except OSError:
            continue
        if "OK" in texto.split() or "ERRO" in texto:
            break
    return texto


def instalar():
    if not eh_exe():
        return "ERRO: a impressora só pode ser instalada pelo programa .exe (não pelo código-fonte)."
    return executa_como_admin(script_instalar(sys.executable, _usuario()))


def remover():
    return executa_como_admin(script_remover())


def reparar():
    return executa_como_admin(script_reparar())
