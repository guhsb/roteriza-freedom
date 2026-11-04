"""Impressora virtual "Romaneio Freedom".

Cria uma impressora do Windows (driver "Microsoft Print To PDF") ligada a uma porta que é um
ARQUIVO (dados\\entrada\\romaneio.pdf). Ao imprimir o romaneio do sistema nessa impressora, o Windows
grava o PDF ali e o programa lê. Uma tarefa agendada abre o programa quando algo é impresso
(evento 307 do log de impressão), para funcionar mesmo com o programa fechado.
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


def impressora_instalada():
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"if (Get-Printer -Name '{_ps(NOME)}' -ErrorAction SilentlyContinue) {{ 'SIM' }}"],
            capture_output=True, text=True, timeout=20, creationflags=SEM_JANELA)
        return "SIM" in (r.stdout or "")
    except Exception:
        return False


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
  if (-not (Get-PrinterPort -Name $porta -ErrorAction SilentlyContinue)) {{ Add-PrinterPort -Name $porta; Log 'porta criada' }}
  if (-not (Get-Printer -Name $nome -ErrorAction SilentlyContinue)) {{
    Add-Printer -Name $nome -DriverName 'Microsoft Print To PDF' -PortName $porta
    Log 'impressora criada'
  }} else {{ Log 'impressora ja existia' }}
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
