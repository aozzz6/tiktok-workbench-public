' TikTok 对标工作台 —— 开机/登录自启（隐藏窗口 + 幂等）
'
' 作用：用户登录时启动 webapp/app.py（FastAPI，端口 8787），
'       供 scheduler 每日 09:00 自动跑 9 账号日更。
'
' 设计要点：
'   1. 幂等——先用 netstat 检查 8787 是否已监听，已跑则直接退出，避免端口冲突
'   2. 隐藏——用 pythonw.exe（无控制台）+ Run(..., 0, False) 不弹窗
'   3. 日志——stdout/stderr 重定向到 logs/autostart.log，便于事后排查
'   4. 路径全 ASCII，避免批处理/脚本编码问题
'
' 手动测试：wscript.exe "D:\workbuddy_project\tiktok\webapp\start_workbench.vbs"
Option Explicit

Dim APP_DIR, PORT, PY, LOG_DIR, LOG
APP_DIR = "D:\workbuddy_project\tiktok\webapp"
PORT    = 8787
PY      = APP_DIR & "\.venv\Scripts\pythonw.exe"
LOG_DIR = APP_DIR & "\logs"
LOG     = LOG_DIR & "\autostart.log"

Dim ws, fso, exec, out, f1
Set ws  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' ── 幂等：端口已在监听则退出 ─────────────────────────────
Set exec = ws.Exec("cmd /c netstat -ano | findstr :" & PORT & " | findstr LISTENING")
out = exec.StdOut.ReadAll()
If Len(Trim(out)) > 0 Then
    WScript.Quit 0
End If

' ── 环境校验：pythonw.exe 必须存在 ──────────────────────
If Not fso.FolderExists(LOG_DIR) Then
    fso.CreateFolder(LOG_DIR)
End If

If Not fso.FileExists(PY) Then
    Set f1 = fso.OpenTextFile(LOG, 8, True)
    f1.WriteLine Now & "  [ERROR] 找不到 " & PY
    f1.Close
    WScript.Quit 1
End If

' ── 启动（隐藏窗口，不等待）──────────────────────────────
ws.CurrentDirectory = APP_DIR
ws.Run "cmd /c """ & PY & """ app.py >> """ & LOG & """ 2>&1", 0, False
