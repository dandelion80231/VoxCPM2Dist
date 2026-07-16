#define MyAppName "VoxCPM2 TTS 中文版"
; 版本号单一数据源：优先用构建脚本传来的 /DVersion，否则回退此处字面量
#ifndef Version
  #define Version "5.3"
#endif
#ifdef VoxNoModel
  #define MyPayload "..\payload_nomodel"
  #define MyOutputBase "VoxCPM2_TTS_v" + Version + "_nomodel_Setup"
#else
  #define MyPayload "..\payload"
  #define MyOutputBase "VoxCPM2_TTS_v" + Version + "_Setup"
#endif
#define MyAssets "assets"

[Setup]
AppId={{VoxCPM2-TTS-ZH-5.0}
AppName={#MyAppName}
AppVersion={#Version}
AppPublisher=VoxCPM2
DefaultDirName={autopf}\VoxCPM2 TTS
DefaultGroupName={#MyAppName}
OutputDir=..\output
OutputBaseFilename={#MyOutputBase}
Compression=lzma2/fast
SolidCompression=no
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\VoxCPM_App.ico
DirExistsWarning=no
DisableDirPage=no
#ifdef VoxNoModel
  ; 无模型版约 1.56GB < 2GB，打包为单个自包含 exe（不分卷、不生成外置 .bin）
#else
  DiskSpanning=yes
  DiskSliceSize=2000000000
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

[Files]
; 预压缩的 app 归档（7z 多线程解压，比 InnoSetup 原生 LZMA 快 2-3x）
Source: "{#MyPayload}\app.7z"; DestDir: "{app}"; Flags: nocompression
; 7z 独立解压器（安装时解压 app.7z 后自动清理）—— 必须为 64 位版本！
; 32 位 7za 在处理 >4GB 的模型文件（model.safetensors 约 4.6GB）时会卡死在收尾阶段，
; 导致安装器无限等待（详见 2026-07-14 修复记录）。x64 7za 还需同目录的 7za.dll / 7zxa.dll。
Source: "{#MyPayload}\7za.exe"; DestDir: "{app}"; Flags: nocompression
Source: "{#MyPayload}\7za.dll"; DestDir: "{app}"; Flags: nocompression
Source: "{#MyPayload}\7zxa.dll"; DestDir: "{app}"; Flags: nocompression
; app.7z 未压缩总大小（字节），安装器据此计算实时解压进度（因 7za 日志在重定向下不实时）
Source: "{#MyPayload}\app_7z_uncompressed_size.txt"; DestDir: "{app}"; Flags: nocompression
; 应用图标
Source: "{#MyAssets}\VoxCPM_App.ico"; DestDir: "{app}"; Flags: nocompression

[Icons]
; 注意：冻结的 .exe 已不再随包发布（原 PyInstaller .spec 已遗失）。
; 现改用随包 python_cuda 直接运行 .py 入口，由以下 .bat 启动器拉起，
; 既保留双击图标体验，又无需重新冻结，且天然包含新增的降噪依赖与模型。
Name: "{group}\{#MyAppName} - 交互菜单"; Filename: "{app}\Scripts\Launch_TTS_Menu.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Comment: "交互式菜单界面（控制台）"
Name: "{group}\{#MyAppName} - 网页界面"; Filename: "{app}\start_web_ui.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Comment: "浏览器图形界面"
Name: "{autodesktop}\{#MyAppName} - 交互菜单"; Filename: "{app}\Scripts\Launch_TTS_Menu.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Tasks: desktopicon
Name: "{autodesktop}\{#MyAppName} - 网页界面"; Filename: "{app}\start_web_ui.bat"; IconFilename: "{app}\VoxCPM_App.ico"; Tasks: desktopicon

[Run]
; 安装完成页"立即运行"复选框（默认勾选），以当前用户启动网页界面
Filename: "cmd.exe"; Parameters: "/c ""{app}\start_web_ui.bat"""; Description: "启动 VoxCPM2 TTS 网页界面"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent
; 安装完成页可选启动交互菜单（默认不勾选）
Filename: "cmd.exe"; Parameters: "/c ""{app}\Scripts\Launch_TTS_Menu.bat"""; Description: "启动 VoxCPM2 TTS 交互菜单"; WorkingDir: "{app}"; Flags: nowait postinstall runascurrentuser skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  CancelRequested: Boolean;
  ExtractBar: TNewProgressBar;
  ExtractLabel: TLabel;

const
  STALL_LIMIT = 2000;    { 2000 * 150ms ≈ 5 分钟无进度 → 判定卡死 }
  OVERALL_LIMIT = 18000; { 18000 * 150ms = 45 分钟总上限（极慢磁盘兜底） }

type
  TMyMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: LongWord;
    lParam: LongInt;
    time: DWORD;
    pt: TPoint;
  end;

function PeekMessage(var Msg: TMyMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: UINT): BOOL; external 'PeekMessageA@user32.dll stdcall';
function TranslateMessage(const Msg: TMyMsg): BOOL; external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const Msg: TMyMsg): LongInt; external 'DispatchMessageA@user32.dll stdcall';
procedure ExitProcess(uExitCode: UINT); external 'ExitProcess@kernel32.dll stdcall';
function GetTickCount: DWORD; external 'GetTickCount@kernel32.dll stdcall';

{ 泵消息：让安装向导在等待期间仍能响应拖拽/最小化/取消 }
procedure PumpMessages;
var
  Msg: TMyMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, 1) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

{ 递归计算目录下所有文件总大小（字节）。不跟随符号链接，遇到失败项目则跳过。 }
function GetFolderSize(const Dir: string): Int64;
var
  FindRec: TFindRec;
  Path: string;
begin
  Result := 0;
  if FindFirst(Dir + '\*', FindRec) then
  begin
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then Continue;
      Path := Dir + '\' + FindRec.Name;
      if FindRec.Attributes and $00000010 <> 0 then
        Result := Result + GetFolderSize(Path)
      else
        Result := Result + Int64(FindRec.SizeLow) + Int64(FindRec.SizeHigh) * 4294967296;
    until not FindNext(FindRec);
  end;
end;

{ 从构建时生成的 size 文件读取 app.7z 未压缩总大小；失败返回 -1。 }
function ReadTotalBytes(const FilePath: string): Int64;
var
  Lines: TArrayOfString;
  s: string;
begin
  Result := -1;
  if LoadStringsFromFile(FilePath, Lines) then
  begin
    if GetArrayLength(Lines) >= 1 then
    begin
      s := Trim(Lines[0]);
      if s <> '' then
      begin
        try
          Result := StrToInt64(s);
        except
          Result := -1;
        end;
      end;
    end;
  end;
end;

{ 获取单个文件大小；失败返回 -1。 }
function GetFileSizeInt64(const Path: string): Int64;
var
  FindRec: TFindRec;
begin
  Result := -1;
  if FindFirst(Path, FindRec) then
    Result := Int64(FindRec.SizeLow) + Int64(FindRec.SizeHigh) * 4294967296;
end;

{ 创建并显示真实的解压进度控件（嵌在安装向导内置进度条下方） }
procedure ShowExtractProgress;
var
  gauge: TNewProgressBar;
  labelTop, barTop: Integer;
begin
  gauge := WizardForm.ProgressGauge;
  labelTop := gauge.Top + gauge.Height + ScaleY(24);
  barTop := labelTop + ScaleY(20);

  { 保持上面“正在安装”标题、描述文字、状态标签始终可见 }
  if WizardForm.PageNameLabel <> nil then
  begin
    WizardForm.PageNameLabel.Caption := '正在安装';
    WizardForm.PageNameLabel.Visible := True;
  end;
  if WizardForm.PageDescriptionLabel <> nil then
  begin
    WizardForm.PageDescriptionLabel.Caption := '安装程序正在安装 VoxCPM2 TTS 中文版到您的计算机，请稍候。';
    WizardForm.PageDescriptionLabel.Visible := True;
  end;
  if WizardForm.StatusLabel <> nil then
  begin
    WizardForm.StatusLabel.Caption := '文件提取完成';
    WizardForm.StatusLabel.Visible := True;
  end;

  ExtractLabel := TLabel.Create(WizardForm);
  ExtractLabel.Parent := WizardForm.ProgressGauge.Parent;
  ExtractLabel.Left := gauge.Left;
  ExtractLabel.Top := labelTop;
  ExtractLabel.Width := gauge.Width;
  ExtractLabel.Caption := '正在解压资源文件，请稍候...';
  ExtractLabel.Visible := True;

  ExtractBar := TNewProgressBar.Create(WizardForm);
  ExtractBar.Parent := WizardForm.ProgressGauge.Parent;
  ExtractBar.Left := gauge.Left;
  ExtractBar.Top := barTop;
  ExtractBar.Width := gauge.Width;
  ExtractBar.Height := gauge.Height;
  ExtractBar.Min := 0;
  ExtractBar.Max := 100;
  ExtractBar.Position := 1; { 初始显示一点，避免被误认为未启动 }
  ExtractBar.Visible := True;
end;

procedure HideExtractProgress;
begin
  if ExtractLabel <> nil then
  begin
    ExtractLabel.Free;
    ExtractLabel := nil;
  end;
  if ExtractBar <> nil then
  begin
    ExtractBar.Free;
    ExtractBar := nil;
  end;
end;

{ 解压完成后清理归档、解压器、临时标记文件与 size 文件 }
procedure RunCleanup(AppDir: string);
var
  ResultCode: Integer;
begin
  Exec('cmd.exe', '/c del /q "' + AppDir + '\app.7z" "' + AppDir + '\7za.exe" "' + AppDir + '\7za.dll" "' + AppDir + '\7zxa.dll" "' + AppDir + '\app_7z_uncompressed_size.txt" "' + AppDir + '\.extract_done" "' + AppDir + '\.extract_error" "' + AppDir + '\.extract_cancelled" "' + AppDir + '\_extract_.bat"',
       AppDir, SW_HIDE, ewNoWait, ResultCode);
end;

{ 取消按钮点击：终止后台 7z，写取消标记，让等待循环退出后再清理退出 }
procedure OnCancelClick(Sender: TObject);
var
  AppDir: string;
  ResultCode: Integer;
begin
  AppDir := ExpandConstant('{app}');
  Exec('taskkill.exe', '/f /im 7za.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('cmd.exe', '/c echo CANCELLED > "' + AppDir + '\.extract_cancelled"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  CancelRequested := True;
end;

{ 等待后台 7z 解压完成，其间更新真实解压进度；带看门狗，绝不无限等待 }
procedure WaitForExtract(AppDir: string);
var
  DoneFile, CancelFile, ErrorFile, BatchPath, SizeFile: string;
  BatchLines: TArrayOfString;
  ResultCode, pct, lastPct, ticks, lastProgressTick, timePct, sizePct: Integer;
  TotalBytes, InitialSize, CurrentSize, DoneBytes, App7zSize: Int64;
  HasTotalBytes: Boolean;
  startTick, elapsedSec, estTotalSec: Integer;
begin
  DoneFile := AppDir + '\.extract_done';
  CancelFile := AppDir + '\.extract_cancelled';
  ErrorFile := AppDir + '\.extract_error';
  BatchPath := AppDir + '\_extract_.bat';
  SizeFile := AppDir + '\app_7z_uncompressed_size.txt';

  { 清除可能残留的旧标记，避免误判 }
  if FileExists(DoneFile) then DeleteFile(DoneFile);
  if FileExists(CancelFile) then DeleteFile(CancelFile);
  if FileExists(ErrorFile) then DeleteFile(ErrorFile);

  ShowExtractProgress;

  { ssPostInstall 期间默认禁用取消按钮，这里强制启用并挂上回调 }
  WizardForm.CancelButton.Enabled := True;
  WizardForm.CancelButton.OnClick := @OnCancelClick;

  { 记录初始目录大小（包含 app.7z/7za 等）。解压新增大小 = 当前目录大小 - 初始大小。 }
  InitialSize := GetFolderSize(AppDir);

  { 读取构建时预记录的 app.7z 未压缩总大小，作为真实进度的分母 }
  TotalBytes := ReadTotalBytes(SizeFile);
  HasTotalBytes := TotalBytes > 0;

  { 估算总时长，作为“时间基线”兜底：保证进度条从 0 秒起就平滑前进，不会长时间停在 1% }
  if HasTotalBytes then
    estTotalSec := 60   { 有真实目录大小时，时间基线仅作兜底；真实进度由目录大小向上校正 }
  else
  begin
    App7zSize := GetFileSizeInt64(AppDir + '\app.7z');
    if App7zSize > 0 then
      estTotalSec := Trunc(App7zSize / (1024 * 1024 * 1024)) * 16  { 约 16 秒/GB（NVMe SSD 实测约 80s/4.9GB） }
    else
      estTotalSec := 120;
    if estTotalSec < 60 then estTotalSec := 60;
  end;

  { 生成临时批处理文件，避免 cmd.exe 参数转义问题 }
  SetArrayLength(BatchLines, 5);
  BatchLines[0] := '@echo off';
  BatchLines[1] := 'setlocal enabledelayedexpansion';
  { 不再使用 -bsp1 日志：7za 在文件重定向下不会实时刷新进度，只保留 -bso0 避免控制台刷屏 }
  BatchLines[2] := '"' + AppDir + '\7za.exe" x "' + AppDir + '\app.7z" -o"' + AppDir + '" -y -bso0';
  { 失败时把错误码写入 .extract_error，供安装器检测并明确报错（而非无限等待） }
  BatchLines[3] := 'if !errorlevel! neq 0 ( echo ERR!errorlevel! > "' + AppDir + '\.extract_error" & exit /b !errorlevel! )';
  BatchLines[4] := 'echo OK > "' + DoneFile + '"';
  SaveStringsToFile(BatchPath, BatchLines, False);

  { 启动批处理进行异步解压（注意：7za 必须为 64 位，否则解压 >4GB 模型文件会卡死） }
  Exec('cmd.exe', '/c "' + BatchPath + '"', AppDir, SW_HIDE, ewNoWait, ResultCode);

  startTick := GetTickCount;
  lastPct := 0;
  lastProgressTick := 0;
  ticks := 0;
  pct := 0;

  while (not FileExists(DoneFile)) and (not FileExists(CancelFile)) and (not FileExists(ErrorFile)) do
  begin
    if CancelRequested then Break;

    { 看门狗①：超过总时限直接判定失败 }
    if ticks > OVERALL_LIMIT then
    begin
      ExtractLabel.Caption := '解压超时（超过总时限）';
      Exec('taskkill.exe', '/f /im 7za.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      MsgBox('资源文件解压未能在预期时间内完成（已等待超过总时限）。' + #13#10 +
             '常见原因：杀毒软件实时防护在写入较大的模型文件（model.safetensors 约 4.6GB）时将其拦截/挂起。' + #13#10 +
             '建议：将安装目标目录加入杀毒软件排除项（或临时关闭实时防护）后，重新运行本安装程序。',
             mbError, MB_OK);
      ExitProcess(1);
    end;

    { 看门狗②：启动 30 秒宽限后，长时间无进度变化判定卡死。 }
    { 注意：现在进度由目录大小驱动，若目录大小长时间不增加也会触发。 }
    if (ticks > 200) and (ticks - lastProgressTick > STALL_LIMIT) then
    begin
      ExtractLabel.Caption := '解压疑似卡死';
      Exec('taskkill.exe', '/f /im 7za.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      MsgBox('资源文件解压长时间无进展，疑似被系统中断（如杀毒软件实时防护拦截了大型模型文件的写入）。' + #13#10 +
             '建议：将安装目标目录加入杀毒软件排除项（或临时关闭实时防护）后，重新运行本安装程序。',
             mbError, MB_OK);
      ExitProcess(1);
    end;

    { 进度计算：时间基线保证进度条从 0 秒起就平滑前进（不再长时间停在 1%）；
      目录实际大小在可用时向上校正出真实进度。两种来源取较大值。 }
    elapsedSec := (GetTickCount - startTick) div 1000;
    if elapsedSec < 0 then elapsedSec := 0;
    timePct := Trunc(elapsedSec * 100 / estTotalSec);
    if timePct < 0 then timePct := 0;
    if timePct > 95 then timePct := 95;

    pct := timePct;
    { 每 3 个 ticks（约 0.45 秒）用目录实际大小校正一次 }
    if HasTotalBytes and ((ticks mod 3 = 0) or (ticks = 0)) then
    begin
      CurrentSize := GetFolderSize(AppDir);
      DoneBytes := CurrentSize - InitialSize;
      if DoneBytes < 0 then DoneBytes := 0;
      if TotalBytes > 0 then
        sizePct := Trunc(DoneBytes * 100 / TotalBytes)
      else
        sizePct := 0;
      if sizePct < 0 then sizePct := 0;
      if sizePct > 95 then sizePct := 95;
      if sizePct > pct then pct := sizePct;  { 真实进度更快时向上取 }
    end;

    if pct < 0 then pct := 0;
    if pct > 100 then pct := 100;
    if pct < lastPct then pct := lastPct;  { 进度只增不减，消除时间基线/目录大小交替时的回退抖动 }

    if pct <> lastPct then
    begin
      ExtractBar.Position := pct;
      ExtractBar.Update;   { 强制立即重绘，确保进度条肉眼可见地前进（不依赖消息泵时序） }
      ExtractLabel.Caption := '正在解压资源文件，请稍候... ' + IntToStr(pct) + '%';
      lastPct := pct;
      lastProgressTick := ticks;
    end;

    { 保持上方文件提取完成状态始终可见 }
    if WizardForm.StatusLabel <> nil then
    begin
      WizardForm.StatusLabel.Caption := '文件提取完成';
      WizardForm.StatusLabel.Visible := True;
    end;

    PumpMessages;
    Sleep(150);
    ticks := ticks + 1;
  end;

  { 7za 自身返回错误 }
  if FileExists(ErrorFile) then
  begin
    ExtractBar.Position := 0;
    ExtractLabel.Caption := '解压失败';
    Exec('taskkill.exe', '/f /im 7za.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    MsgBox('资源文件解压过程中 7za 返回了错误，安装无法继续。' + #13#10 +
           '请检查安装目标磁盘是否有足够空间，或临时关闭杀毒软件实时防护后重新运行本安装程序。',
           mbError, MB_OK);
    ExitProcess(1);
  end;

  { 收尾 }
  if FileExists(CancelFile) or CancelRequested then
  begin
    ExtractBar.Position := 0;
    ExtractLabel.Caption := '已取消';
  end
  else
  begin
    ExtractBar.Position := 100;
    ExtractLabel.Caption := '解压完成，正在收尾...';
  end;
  PumpMessages;
  Sleep(400);

  HideExtractProgress;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, CancelFile: string;
  ResultCode: Integer;
begin
  { 安装后阶段：真实解压进度，完成后清理，避免完成页“假完成” }
  if CurStep = ssPostInstall then
  begin
    AppDir := ExpandConstant('{app}');
    WaitForExtract(AppDir);
    CancelFile := AppDir + '\.extract_cancelled';
    if FileExists(CancelFile) then
    begin
      RunCleanup(AppDir);
      MsgBox('安装已取消。', mbInformation, MB_OK);
      ExitProcess(1);
    end
    else
    begin
      RunCleanup(AppDir);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  AppDir: string;
begin
  { 完成页：解压已在 ssPostInstall 中完成，只做保险清理 }
  if CurPageID = wpFinished then
  begin
    AppDir := ExpandConstant('{app}');
    if FileExists(AppDir + '\.extract_done') then
      RunCleanup(AppDir);
  end;
end;
